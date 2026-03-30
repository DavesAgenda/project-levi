# Sovereign OS Starter Kit

**Version**: 1.0 (Neutral)
**Concept**: A "Barebones" Operating System for the Sovereign Professional.

This kit provides the file structures, agent personas, and business rhythms needed to run a professional-grade One-Person Business using AI agents. It is **IDE Agnostic** and designed to be "dropped in" to any agentic environment (OpenCode, Claude Code, etc.).

## 📂 Directory Structure

```text
Starter_Kit/
├── agents.md             # The Team Manifest
├── .agent/
│   ├── skills/           # The specialized capability files
│   └── workflows/        # The rhythm definitions
├── 00_Context/           # Your Memory (Empty Skeleton)
├── Connect/              # The Connectivity Layer (Optional)
└── README_Starter_Kit.md # This file
```

## 🚀 Quick Start

1.  **Open Folder**: Open this `Starter_Kit` folder in your IDE (Cursor, VS Code, etc.).
2.  **Initialize**:
    Ask your AI agent: *"Read `.agent/workflows/Onboarding.md` and guide me through the setup."*
3.  **Start a Rhythm**:
    Ask your agent: *"Run the [[Dawn_Rhythm|Dawn Rhythm]]."*

## 🧩 The Connectivity Layer (Advanced)

To give your agents real-world power (Email/Calendar), you need a "Bridge."

**We provide a Reference Implementation using n8n**, but you can use Make, Zapier, or any webhook-capable platform.

**Read the Guide**: `Starter_Kit/Connect/README.md`

This guide explains:
-   **Local Brain, Cloud Hands**: Why we separate logic from execution.
-   **Webhooks**: How to set up secure "Fire-and-Forget" actions.
-   **Patterns**: Best practices for integrating standard APIs.
