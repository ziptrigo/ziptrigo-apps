# Web fonts

Self-hosted by `core/css/site.css` (`@font-face`, `font-display: swap`, latin subset). Both families
are licensed under the SIL Open Font License 1.1.

| File | Family | Weights | Source |
|---|---|---|---|
| `figtree-latin.woff2` | Figtree (variable) | 300–900 | https://fonts.google.com/specimen/Figtree |
| `ibm-plex-mono-400-latin.woff2` | IBM Plex Mono | 400 | https://fonts.google.com/specimen/IBM+Plex+Mono |
| `ibm-plex-mono-500-latin.woff2` | IBM Plex Mono | 500 | same |

Until these files are present the Tailwind font stacks fall back to the system fonts
(`ui-sans-serif, system-ui, ...` and `ui-monospace, ...`), so nothing breaks, but the site doesn't yet
show Figtree. Put the licence texts (`OFL-Figtree.txt`, `OFL-IBMPlexMono.txt`) in this folder with the files.

To fetch them: open the Google Fonts CSS for each family in a browser (or `curl` it with a modern
`User-Agent`), take the `/* latin */` block's `src: url(...woff2)` and save that file under the name above.
