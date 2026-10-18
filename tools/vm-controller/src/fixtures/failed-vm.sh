#!/bin/sh
if [ "$1" = --describe ]; then printf '%s' '{"scenario":"netns-batch"}'; exit 0; fi; printf failure; exit 1
