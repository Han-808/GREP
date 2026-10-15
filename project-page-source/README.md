# GREP project website

The public site is served from `project-page/` at
[han-808.github.io/GREP](https://han-808.github.io/GREP/).
It is a self-contained static website; no evaluator, server API, Blender, or
model credentials run in the browser.

## Editing

- Edit the homepage and Leaderboard HTML, CSS, and JavaScript in `project-page/`.
- Edit Examples in the two readable templates in this directory, then run
  `python3 project-page-source/rebuild_examples.py` from the repository root.
- Static display JSON lives in `project-page/`. The build restores referenced
  scene images from an existing repository Release asset, verifying their hashes
  against `images-manifest.json` before publishing them.
- The GitHub Pages workflow also rebuilds Examples before deploying.

Preview from the repository root with `python3 -m http.server 8878`, then open
`http://127.0.0.1:8878/project-page/`. All navigation and assets use relative URLs,
so the same files work under the GitHub project path.

On a fresh checkout, first run `python3 scripts/fetch_project_page_images.py`.
The downloaded images stay ignored by Git and are included in the Pages artifact.

## Display scope

Examples and Leaderboard contain Single-room and rectangular Multi-room model
results. Explicitly Exclude-labeled model variants, Complex Layout, Agent, and
Pipeline are omitted. HY4 Preview remains. Original scores are preserved.
Deducted-object disclosures list distinct objects only for metrics with a current
deduction; missing current attribution is not filled with historical findings.

The interactive homepage room is a staged illustration with pre-authored findings
and camera movement. Asset attribution is in `project-page/asset-credits.html`.
The page makes no conference or acceptance claim.

## Updating from the local authoring snapshot

`scripts/export_project_page.py` creates a fresh static export from the ignored
authoring workspace and captured display snapshot. It checks retained scores,
current object IDs, and the local asset dependency closure. Pass an empty output
directory with `--output`; `--website`, `--snapshot`, `--audit`, and
`--editable-output` are also supported. Raw experiment archives and credentials
are not part of the website.
