CXX := clang++
CPPFLAGS := -Iinclude
CXXFLAGS := -std=c++17 -O2 -Wall -Wextra -Wpedantic -Werror
BUILD := build

.PHONY: all test sanitize demo benchmark clean
all: $(BUILD)/flightdeck

$(BUILD):
	mkdir -p $(BUILD)

$(BUILD)/flightdeck: src/main.cpp src/flightdeck.cpp include/flightdeck.hpp | $(BUILD)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -DFLIGHTDECK_BUILD_FLAGS='"$(CXXFLAGS)"' src/flightdeck.cpp src/main.cpp -o $@

$(BUILD)/tests: tests/test_flightdeck.cpp src/flightdeck.cpp include/flightdeck.hpp | $(BUILD)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) src/flightdeck.cpp tests/test_flightdeck.cpp -o $@

test: $(BUILD)/flightdeck $(BUILD)/tests
	./$(BUILD)/tests
	python3 tests/integration.py ./$(BUILD)/flightdeck
	python3 -m unittest discover -s tests -p test_report.py

sanitize: | $(BUILD)
	$(CXX) $(CPPFLAGS) -std=c++17 -O1 -g -Wall -Wextra -Wpedantic -Werror -fsanitize=address,undefined -fno-omit-frame-pointer src/flightdeck.cpp tests/test_flightdeck.cpp -o $(BUILD)/tests-sanitize
	./$(BUILD)/tests-sanitize
	$(CXX) $(CPPFLAGS) -std=c++17 -O1 -g -Wall -Wextra -Wpedantic -Werror -fsanitize=address,undefined -fno-omit-frame-pointer src/flightdeck.cpp src/main.cpp -o $(BUILD)/flightdeck-sanitize
	python3 tests/integration.py ./$(BUILD)/flightdeck-sanitize

demo: all
	python3 scripts/generate_fixture.py
	./$(BUILD)/flightdeck record examples/synthetic.csv $(BUILD)/synthetic.fdlog --report $(BUILD)/record.json
	./$(BUILD)/flightdeck replay $(BUILD)/synthetic.fdlog --report docs/report.json
	python3 scripts/render_report.py docs/report.json docs/index.html

benchmark: all
	python3 scripts/benchmark.py ./$(BUILD)/flightdeck

clean:
	rm -rf $(BUILD)
