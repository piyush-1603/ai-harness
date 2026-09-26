# ai-harness

An autonomous coding harness based on `mini-swe-agent`.

## Overview

This project builds upon the minimal AI software engineering agent framework from `mini-swe-agent`, providing an orchestration layer and recovery tracking for autonomous coding tasks.

## Getting Started

### Installation

Install the required dependencies using pip or your preferred package manager:

```bash
pip install -e .
```

### Usage

You can start the orchestrator via the CLI entrypoint:

```bash
mini-swe-agent
```

## Structure

- **src/orchestrator/**: Core orchestration logic, including state machines and error recovery.
- **src/minisweagent/**: The underlying minimal SWE agent implementation.
