#!/bin/bash
# Source shell environment for API keys
source ~/.zshrc 2>/dev/null || true

cd /Users/erisdothard/OpenJarvis
exec /Users/erisdothard/.local/bin/uv run jarvis serve
