// Extension: project-change-summary
// Displays the project change summary in an interactive canvas.
//
// This single-file skeleton is a starting point. For more complex canvases
// (multiple actions with non-trivial logic, shared state, a custom renderer,
// etc.) prefer splitting things out: move each action handler into its own
// function, extract `open`/`onClose` into helpers, and pull large units
// (renderer assets, schema definitions, shared utilities) into sibling files
// imported from this entry point. Keep extension.mjs focused on wiring.

import { createServer } from "node:http";
import { joinSession, createCanvas } from "@github/copilot-sdk/extension";

// One local HTTP server per open canvas instance. Each instance gets its own
// ephemeral port so multiple canvases (or multiple opens of the same canvas)
// don't collide. Replace this with your real renderer — point a static-file
// server, a Vite/Next dev server, or any framework you like at the same URL.
const servers = new Map();

const defaultSummary = {
    title: "Slide-Video Lecture Analysis Toolkit",
    subtitle: "Summary of the project changes made during this session",
    sections: [
        {
            heading: "Runtime and portability",
            items: [
                "Centralized repository paths and improved Colab/local portability.",
                "Added safer video streaming, CPU-friendly Whisper handling, and clearer YouTube authentication errors.",
                "Added optional YTDLP_COOKIES_FILE support."
            ]
        },
        {
            heading: "Video upload and saved videos",
            items: [
                "Fixed the upload hang caused by treating uploaded-file hashes as YouTube IDs.",
                "Kept Whisper transcription in the background.",
                "Added a Saved uploads dropdown and direct retrieval of previously uploaded videos.",
                "New uploads retain their original filenames in metadata."
            ]
        },
        {
            heading: "Slide detection",
            items: [
                "Separated slide detection from video loading.",
                "Added the Detect Slides workflow with visible progress and retryable errors.",
                "Added a sensitivity slider and selectable Content cuts or Frame difference detection methods.",
                "Frame difference detection can identify gradual changes, fades, and animations."
            ]
        },
        {
            heading: "Gemini summaries and chapters",
            items: [
                "Added Gemini topic-based chapter generation with GEMINI_MODEL override support.",
                "Updated the default Gemini model to gemini-3.6-flash.",
                "Added retry behavior and preserved existing chapters when generation fails.",
                "Added explicit fixed-interval chapter export without silently using it as a fallback."
            ]
        },
        {
            heading: "Interface and analysis",
            items: [
                "Moved summary and export controls into Chapters.",
                "Moved Detect Slides and its controls into Slide Changes.",
                "Preserved the Interactive Layer and improved OCR/scene-processing feedback."
            ]
        }
    ]
};

function escapeHtml(value) {
    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
}

function normalizeSummary(input) {
    if (!input || typeof input !== "object") return defaultSummary;
    return {
        title: input.title || defaultSummary.title,
        subtitle: input.subtitle || defaultSummary.subtitle,
        sections: Array.isArray(input.sections) && input.sections.length
            ? input.sections
            : defaultSummary.sections,
    };
}

function renderHtml(instanceId, input) {
    const summary = normalizeSummary(input);
    const sections = summary.sections.map((section) => `
      <section class="section">
        <h2>${escapeHtml(section.heading || "Changes")}</h2>
        <ul>${(Array.isArray(section.items) ? section.items : [])
            .map((item) => `<li>${escapeHtml(item)}</li>`)
            .join("")}</ul>
      </section>
    `).join("");
    return `<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>${escapeHtml(summary.title)}</title>
    <style>
      :root { color-scheme: light dark; }
      body { margin: 0; padding: 24px; background: var(--background-color-default, #fff); color: var(--text-color-default, #1f2328); font-family: var(--font-sans, system-ui, sans-serif); line-height: 1.5; }
      main { max-width: 900px; margin: 0 auto; }
      h1 { margin: 0; font-size: 26px; }
      .subtitle { color: var(--text-color-muted, #656d76); margin: 6px 0 24px; }
      .section { border-top: 1px solid var(--border-color-default, #d0d7de); padding: 16px 0; }
      h2 { font-size: 18px; margin: 0 0 8px; }
      li { margin: 6px 0; }
      .meta { color: var(--text-color-muted, #656d76); font-size: 12px; margin-top: 28px; }
    </style>
  </head>
  <body>
    <main>
      <h1>${escapeHtml(summary.title)}</h1>
      <p class="subtitle">${escapeHtml(summary.subtitle)}</p>
      ${sections}
      <p class="meta">Canvas instance: ${escapeHtml(instanceId)}</p>
    </main>
  </body>
</html>`;
}

async function startServer(instanceId, input) {
    const server = createServer((req, res) => {
        res.setHeader("Content-Type", "text/html; charset=utf-8");
        res.end(renderHtml(instanceId, input));
    });
    // Port 0 = let the OS pick a free ephemeral port. Bind to loopback only.
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    const address = server.address();
    const port = typeof address === "object" && address ? address.port : 0;
    return { server, url: `http://127.0.0.1:${port}/` };
}

const session = await joinSession({
    canvases: [
        createCanvas({
            id: "project-change-summary",
            displayName: "project-change-summary",
            description: "Displays an interactive summary of the project changes.",
            inputSchema: {
                type: "object",
                properties: {
                    title: { type: "string" },
                    subtitle: { type: "string" },
                    sections: { type: "array" }
                },
                additionalProperties: false
            },
            actions: [
                {
                    name: "get_summary",
                    description: "Returns the current project change summary represented by this canvas.",
                    handler: async (ctx) => {
                        return normalizeSummary(ctx.input);
                    },
                },
            ],
            // Called when the agent or host opens the canvas. We boot a local
            // HTTP server on an ephemeral port and hand its URL back to the
            // host so it can render the canvas. Re-opens with the same
            // instanceId reuse the existing server.
            open: async (ctx) => {
                let entry = servers.get(ctx.instanceId);
                if (!entry) {
                    entry = await startServer(ctx.instanceId, ctx.input);
                    servers.set(ctx.instanceId, entry);
                }
                return {
                    title: "project-change-summary",
                    url: entry.url,
                };
            },
            // Tear the per-instance server down when the canvas is closed so
            // ports are not leaked across the lifetime of the extension.
            onClose: async (ctx) => {
                const entry = servers.get(ctx.instanceId);
                if (entry) {
                    servers.delete(ctx.instanceId);
                    await new Promise((resolve) => entry.server.close(() => resolve()));
                }
            },
        }),
    ],
});
