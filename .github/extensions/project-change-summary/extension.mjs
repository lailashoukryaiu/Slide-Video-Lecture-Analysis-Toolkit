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
    const totalItems = summary.sections.reduce(
        (count, section) => count + (Array.isArray(section.items) ? section.items.length : 0),
        0
    );
    const sections = summary.sections.map((section, sectionIndex) => {
        const items = Array.isArray(section.items) ? section.items : [];
        const itemMarkup = items.map((item, itemIndex) => `
          <li class="change" data-search="${escapeHtml(`${section.heading || ""} ${item}`.toLowerCase())}">
            <label>
              <input type="checkbox" data-item="${sectionIndex}-${itemIndex}" />
              <span>${escapeHtml(item)}</span>
            </label>
          </li>
        `).join("");
        return `
      <section class="section" data-section>
        <button class="section-toggle" type="button" aria-expanded="true">
          <span><span class="chevron">⌄</span> ${escapeHtml(section.heading || "Changes")}</span>
          <span class="section-count">${items.length}</span>
        </button>
        <ul>${itemMarkup}</ul>
      </section>
        `;
    }).join("");
    const copyText = summary.sections.map((section) =>
        `${section.heading || "Changes"}\n${(Array.isArray(section.items) ? section.items : [])
            .map((item) => `- ${item}`).join("\n")}`
    ).join("\n\n");
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
      .subtitle { color: var(--text-color-muted, #656d76); margin: 6px 0 18px; }
      .toolbar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 18px; }
      input[type="search"] { flex: 1 1 220px; min-width: 180px; border: 1px solid var(--border-color-default, #d0d7de); border-radius: 6px; padding: 9px 11px; background: var(--background-color-default, #fff); color: inherit; }
      button { border: 1px solid var(--border-color-default, #d0d7de); border-radius: 6px; padding: 8px 11px; background: var(--background-color-default, #fff); color: inherit; cursor: pointer; }
      button:hover { background: var(--background-color-muted, #f6f8fa); }
      .progress { display: flex; justify-content: space-between; color: var(--text-color-muted, #656d76); font-size: 13px; margin-bottom: 14px; }
      .progress-bar { height: 6px; border-radius: 99px; background: var(--background-color-muted, #f6f8fa); overflow: hidden; margin-bottom: 20px; }
      .progress-bar span { display: block; height: 100%; width: 0; background: var(--true-color-blue, #0969da); transition: width .2s ease; }
      .section { border: 1px solid var(--border-color-default, #d0d7de); border-radius: 8px; margin: 10px 0; overflow: hidden; }
      .section-toggle { display: flex; justify-content: space-between; align-items: center; width: 100%; border: 0; border-radius: 0; padding: 13px 15px; text-align: left; font-size: 16px; font-weight: 600; }
      .chevron { display: inline-block; width: 18px; transition: transform .2s ease; }
      .section.collapsed .chevron { transform: rotate(-90deg); }
      .section-count { color: var(--text-color-muted, #656d76); font-size: 12px; font-weight: 400; }
      .section ul { list-style: none; margin: 0; padding: 4px 15px 12px 42px; }
      .section.collapsed ul { display: none; }
      .change { margin: 9px 0; }
      .change label { display: flex; gap: 9px; align-items: flex-start; cursor: pointer; }
      .change input { margin-top: 5px; accent-color: var(--true-color-blue, #0969da); }
      .change input:checked + span { color: var(--text-color-muted, #656d76); text-decoration: line-through; }
      .change.hidden { display: none; }
      .empty { display: none; padding: 16px; border: 1px dashed var(--border-color-default, #d0d7de); border-radius: 8px; color: var(--text-color-muted, #656d76); }
      .meta { color: var(--text-color-muted, #656d76); font-size: 12px; margin-top: 28px; }
    </style>
  </head>
  <body>
    <main>
      <h1>${escapeHtml(summary.title)}</h1>
      <p class="subtitle">${escapeHtml(summary.subtitle)}</p>
      <div class="toolbar">
        <input id="search" type="search" placeholder="Filter changes..." aria-label="Filter changes" />
        <button id="expand" type="button">Collapse all</button>
        <button id="copy" type="button">Copy summary</button>
      </div>
      <div class="progress"><span id="progress-label">0 of ${totalItems} changes reviewed</span><span id="visible-label">${totalItems} shown</span></div>
      <div class="progress-bar" aria-hidden="true"><span id="progress-bar"></span></div>
      ${sections}
      <p class="empty" id="empty">No changes match your filter.</p>
      <p class="meta">Canvas instance: ${escapeHtml(instanceId)}</p>
    </main>
    <script>
      const storageKey = "project-change-summary:${escapeHtml(instanceId)}";
      const checks = JSON.parse(localStorage.getItem(storageKey) || "{}");
      const boxes = [...document.querySelectorAll("[data-item]")];
      const sections = [...document.querySelectorAll("[data-section]")];
      const search = document.querySelector("#search");
      const progressLabel = document.querySelector("#progress-label");
      const progressBar = document.querySelector("#progress-bar");
      const visibleLabel = document.querySelector("#visible-label");
      const empty = document.querySelector("#empty");
      const expand = document.querySelector("#expand");
      const copy = document.querySelector("#copy");

      boxes.forEach((box) => {
        box.checked = Boolean(checks[box.dataset.item]);
        box.addEventListener("change", () => {
          checks[box.dataset.item] = box.checked;
          localStorage.setItem(storageKey, JSON.stringify(checks));
          updateProgress();
        });
      });

      function updateProgress() {
        const reviewed = boxes.filter((box) => box.checked).length;
        const visible = document.querySelectorAll(".change:not(.hidden)").length;
        progressLabel.textContent = reviewed + " of " + boxes.length + " changes reviewed";
        progressBar.style.width = (boxes.length ? reviewed / boxes.length * 100 : 0) + "%";
        visibleLabel.textContent = visible + " shown";
        empty.style.display = visible ? "none" : "block";
      }

      search.addEventListener("input", () => {
        const query = search.value.trim().toLowerCase();
        document.querySelectorAll(".change").forEach((item) => {
          item.classList.toggle("hidden", Boolean(query) && !item.dataset.search.includes(query));
        });
        sections.forEach((section) => {
          const matches = section.querySelectorAll(".change:not(.hidden)").length;
          section.style.display = matches ? "" : "none";
        });
        updateProgress();
      });

      document.querySelectorAll(".section-toggle").forEach((button) => {
        button.addEventListener("click", () => {
          const section = button.closest(".section");
          const collapsed = section.classList.toggle("collapsed");
          button.setAttribute("aria-expanded", String(!collapsed));
        });
      });

      expand.addEventListener("click", () => {
        const shouldCollapse = sections.some((section) => !section.classList.contains("collapsed"));
        sections.forEach((section) => {
          section.classList.toggle("collapsed", shouldCollapse);
          section.querySelector(".section-toggle").setAttribute("aria-expanded", String(!shouldCollapse));
        });
        expand.textContent = shouldCollapse ? "Expand all" : "Collapse all";
      });

      copy.addEventListener("click", async () => {
        await navigator.clipboard.writeText(${JSON.stringify(copyText)});
        copy.textContent = "Copied";
        setTimeout(() => { copy.textContent = "Copy summary"; }, 1200);
      });

      updateProgress();
    </script>
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
