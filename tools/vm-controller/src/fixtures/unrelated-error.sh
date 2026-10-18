#!/bin/sh
if [ "$1" = --describe ]; then printf '%s' '{"scenario":"netns-batch","watchdog_fixture_supported":true}'; exit 0; fi; printf unrelated; exit 3
