PACTICIPANT := pactflow-example-drift-mcp

# The MCP definition document is the provider contract published to PactFlow.
DEFINITION := notes.mcp.yaml
TESTCASES := drift/notes.testcases.yaml

# Ports for the three server modes, so they can run side by side.
PORT ?= 8099
LEGACY_PORT ?= 8098
DRIFT_PORT ?= 8097

# Drift writes its results bundle and JUnit report under here. Only the default
# `test` run writes to the top level — that is the run published to PactFlow.
OUTPUT_DIR := output
RESULTS_DIR := $(OUTPUT_DIR)/results

# The Pact CLI is used only to publish the contract to PactFlow.
PACT_CLI := docker run --rm -v "$(PWD)":/app/tmp -e PACT_BROKER_BASE_URL -e PACT_BROKER_TOKEN pactfoundation/pact:latest
MOUNT := /app/tmp

GIT_COMMIT ?= $(shell git rev-parse --short HEAD)
GIT_BRANCH ?= $(shell git rev-parse --abbrev-ref HEAD)

.PHONY: all test test_legacy test_stdio test_drift _drift_run test_safe \
        server ci fake_ci publish_provider_contract clean

all: test

# Starts the HTTP server, waits for it to accept connections, runs Drift against
# it, then stops it. Drift's exit code is the target's exit code.
#
#   $(1) extra server arguments   $(2) port   $(3) output directory
#   $(4) extra `drift verify` arguments
define http_verify
	@python3 server/server.py --port $(2) $(1) & \
	SERVER_PID=$$!; \
	trap "kill $$SERVER_PID 2>/dev/null" EXIT; \
	for i in $$(seq 1 40); do \
	  nc -z 127.0.0.1 $(2) >/dev/null 2>&1 && break; \
	  sleep 0.25; \
	done; \
	drift verify \
	  --server-url http://127.0.0.1:$(2)/mcp \
	  --test-files $(TESTCASES) \
	  --output-dir $(3) \
	  --generate-result $(4)
endef

## =====================
## Test
## =====================

## Verify the server against notes.mcp.yaml over Streamable HTTP. This is the
## run whose results get published to PactFlow.
test: clean
	$(call http_verify,,$(PORT),$(OUTPUT_DIR))

## Same operations against a server speaking the older `initialize` handshake.
## Drift negotiates the era itself, so the testcases are unchanged.
test_legacy:
	$(call http_verify,--legacy,$(LEGACY_PORT),$(OUTPUT_DIR)/legacy)

## Same operations over the stdio transport. There is no server to start —
## Drift spawns it, once per run, and holds it open for every operation.
test_stdio:
	drift verify \
	  --server-url "stdio:///python3?arg=server/stdio_server.py" \
	  --test-files $(TESTCASES) \
	  --output-dir $(OUTPUT_DIR)/stdio \
	  --generate-result

## Skip operations the plugin tagged `destructive` (delete_note is tagged
## automatically because it declares annotations.destructiveHint: true).
test_safe:
	$(call http_verify,,$(PORT),$(OUTPUT_DIR)/safe,--tags '!destructive')

## Run the server in drift mode — it deliberately diverges from the definition.
## `drift verify` is SUPPOSED to fail here, so its non-zero exit is expected and
## ignored; what actually gets asserted is that it reported the specific
## findings we expect. A non-zero exit alone would also be produced by an auth
## or connection error, which would make this target pass while testing nothing.
test_drift:
	@rm -rf $(OUTPUT_DIR)/drift
	-@$(MAKE) --no-print-directory _drift_run
	@python3 scripts/assert_drift_detected.py $(OUTPUT_DIR)/drift

_drift_run:
	$(call http_verify,--drift,$(DRIFT_PORT),$(OUTPUT_DIR)/drift)

## Run the HTTP server in the foreground (useful for poking at it by hand).
server:
	python3 server/server.py --port $(PORT)

## =====================
## CI
## =====================

## Verify the server, then publish the definition and the Drift results to
## PactFlow — whether verification passed or failed.
ci:
	@if $(MAKE) test; then \
		EXIT_CODE=0 $(MAKE) publish_provider_contract; \
	else \
		EXIT_CODE=1 $(MAKE) publish_provider_contract; \
	fi

## Run the CI flow locally, with CI-like environment variables.
fake_ci:
	CI=true \
	GIT_COMMIT=`git rev-parse --short HEAD`-`date +%s` \
	GIT_BRANCH=`git rev-parse --abbrev-ref HEAD` \
	$(MAKE) ci

## =====================
## PactFlow
## =====================

## Publish notes.mcp.yaml to PactFlow as an MCP provider contract, along with
## the Drift verification results that show the server conforms to it.
publish_provider_contract:
	@echo ""
	@echo "========== Publishing provider contract + verification results =========="
	@echo ""
	@RESULTS_FILE=$$(find $(RESULTS_DIR) -maxdepth 1 -name 'verification.*.result' -type f | head -1); \
	if [ -z "$$RESULTS_FILE" ]; then \
	  echo "No Drift verification results found in $(RESULTS_DIR) — run 'make test' first"; \
	  exit 1; \
	fi; \
	$(PACT_CLI) pactflow publish-provider-contract \
	  $(MOUNT)/$(DEFINITION) \
	  --provider $(PACTICIPANT) \
	  --provider-app-version $(GIT_COMMIT) \
	  --branch $(GIT_BRANCH) \
	  --specification mcp \
	  --content-type application/yaml \
	  --verification-exit-code=$${EXIT_CODE:-0} \
	  --verification-results "$(MOUNT)/$$RESULTS_FILE" \
	  --verification-results-content-type application/vnd.smartbear.drift.result \
	  --verifier drift \
	  --verifier-version "$$(drift --version)"

## =====================
## Misc
## =====================

clean:
	rm -rf $(OUTPUT_DIR) && mkdir -p $(RESULTS_DIR)
