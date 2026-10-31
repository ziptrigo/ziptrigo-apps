# Web fonts

Self-hosted by `core/css/site.css` (`@font-face`, `font-display: swap`, latin subset). Both families
are licensed under the SIL Open Font License 1.1.

| File | Family | Weights | Source |
|---|---|---|---|
| `figtree-latin.woff2` | Figtree (variable) | 300–900 | https://fonts.google.com/specimen/Figtree |
| `ibm-plex-mono-400-latin.woff2` | IBM Plex Mono | 400 | https://fonts.google.com/specimen/IBM+Plex+Mono |
| `ibm-plex-mono-500-latin.woff2` | IBM Plex Mono | 500 | same |

The licence texts are next to the files (`OFL-Figtree.txt`, `OFL-IBMPlexMono.txt`). If a file is ever
missing, the Tailwind font stacks fall back to the system fonts (`ui-sans-serif, system-ui, ...` and
`ui-monospace, ...`), so nothing breaks.

To refresh them: fetch the Google Fonts CSS for each family with a modern `User-Agent` (for example
`https://fonts.googleapis.com/css2?family=Figtree:wght@300..900`), take the `/* latin */` block's
`src: url(...woff2)` and save that file under the name above.
