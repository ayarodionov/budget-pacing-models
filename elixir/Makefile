# Builds priv/pacing_math_nif.so for PacingMathNif.
#   make            build the NIF
#   make test       golden tests: PacingMath and PacingMathNif against scipy,
#                   PacingController and TokenCap against Python traces
#   make bench      compare pure Elixir and NIF speed
# In a mix project, hook this Makefile in with the elixir_make compiler.

ERTS_INCLUDE_DIR ?= $(shell erl -noshell -eval 'io:format("~s/erts-~s/include", [code:root_dir(), erlang:system_info(version)]), halt().')

CFLAGS ?= -O2 -std=c11 -Wall -Wextra -Wno-unused-parameter -fPIC
UNAME_S := $(shell uname -s)
ifeq ($(UNAME_S),Darwin)
  LDFLAGS += -dynamiclib -undefined dynamic_lookup
else
  LDFLAGS += -shared
endif

NIF = priv/pacing_math_nif.so

all: $(NIF)

$(NIF): c_src/pacing_math_nif.c
	@mkdir -p priv
	$(CC) $(CFLAGS) -I$(ERTS_INCLUDE_DIR) $(LDFLAGS) -o $@ $< -lm

SRC = -r pacing_math.ex -r pacing_math_nif.ex -r pacing_controller.ex -r token_cap.ex

test: $(NIF)
	elixir $(SRC) pacing_math_test.exs
	elixir $(SRC) pacing_controller_test.exs
	elixir $(SRC) token_cap_test.exs

bench: $(NIF)
	elixir $(SRC) bench.exs

clean:
	rm -rf priv

.PHONY: all test bench clean
