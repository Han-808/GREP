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
- Run `python3 project-page-source/build_public_scores.py` after updating display
  data. It derives multi-room ranking scores from layouts whose required rooms
  all have scores. The GitHub Pages workflow applies this scoring rule and rebuilds
  Examples before deploying.
- The main Leaderboard averages use Table 1, transcribed in `paper-table1.json`.
  Run `python3 project-page-source/build_paper_leaderboard.py` to validate the
  supplied PDF fingerprint and generate `project-page/paper_leaderboard.json`.
  Overall and Principle views use these paper averages and sample counts. The
  Metric view uses the available case exports and their actual scored counts.
  Missing case-level results or renders are never inferred from paper means.
- Rebuilding Examples also versions local scripts and styles by their content so
  a browser cannot combine a new page with an earlier script revision.
- For homepage-only changes, run `python3 project-page-source/version_homepage.py`.
  It applies the same content-hash query strings to homepage CSS/JS references
  without rewriting Examples, Leaderboard, or score data. If a local module dependency
  changes, also refresh its import query string before versioning the entry page.

Preview from the repository root with `python3 -m http.server 8878`, then open
`http://127.0.0.1:8878/project-page/`. All navigation and assets use relative URLs,
so the same files work under the GitHub project path.

On a fresh checkout, first run `python3 scripts/fetch_project_page_images.py`.
The downloaded images stay ignored by Git and are included in the Pages artifact.

## Display scope

Examples and Leaderboard contain Single-room and rectangular Multi-room model
results. Explicitly Exclude-labeled model variants, Complex Layout, Agent, and
Pipeline are omitted. HY4 Preview remains. Original room and layout scores are
preserved. Model averages use the paper's eligible-layout cohort: a layout with a
missing room score remains browsable but is not ranked. The score-count column
counts ranked layouts, not generated layouts.
Deducted-object disclosures list distinct objects only for metrics with a current
deduction; missing current attribution is not filled with historical findings.

The interactive homepage room is a staged illustration with pre-authored findings
and camera movement. Asset attribution is in `project-page/asset-credits.html`.
The page makes no conference or acceptance claim.

The Function illustration uses a hinged cabinet door and an ottoman. The original
arrangement stops the door at about 13 degrees; the adjusted arrangement allows
90 degrees. `cabinet-door-geometry.js` finds first contact across the swing from
the rendered door/handle and obstacle geometry. The opening animation illustrates
the use consequence; it does not claim that the paper's evaluator manipulates
articulated furniture. The UI obtains a front view, runs the door illustration,
then presents its object-level diagnosis. Mode changes reset the door and disable
testing until the furniture finishes moving.

The homepage starts with the title, all three evaluation principles, a short
human-agreement result, and the live top-three leaderboard preview. Seven numbered sections follow:
1. What GREP does; 2. Why do we need GREP; 3. How does GREP do it;
4. Principles and workflow; 5. Inspect the room, including links to evaluated
scenes; 6. What GREP reveals; 7. Bonus: reports for revision. Keep each explanation short and distinguish
principled, adaptive discovery from proactive evidence. `assets/figures/` contains the original
vector figures and web PNGs. `paper-framework-source.json` records Figure 2
provenance; `paper-evidence-source.json` records the full Figure 1 teaser and its
four unmodified crops. Clicking a figure opens its vector version.
What GREP does gives most of its width to evaluation: three principles, eight
metrics, and score/report outputs. Principles and workflow uses a connected native
diagram with a visible evidence-acquisition loop; Figure 2 stays linked. The same
A–C tags identify interactive checks. Principles feed the room-specific checks;
the evidence decision separates judgment from further inspection, with unresolved
gaps retained when the inspection budget is reached.
Preserve the `Human-Use` label in surrounding copy.

The hero links directly to the room demo and scopes its rounded human-agreement
result to 15 scene pairs. Its three thumbnail questions introduce the principles;
the full comparator judgments appear once in Why.

`room-loader.js` owns loading recovery independently of the 3D module. A 20-second
delay shows a static preview and retry while the pending load continues; it is not
treated as a missing asset. Successful late loads replace the poster. Module,
asset or WebGL failures retain the poster and disable unavailable room controls.
Retry reloads the page to avoid duplicate renderers and event listeners.

The contribution comparisons are scoped to the paper's Figure 1 examples, with
specific comparator outcomes and source links. Do not generalize these examples
into claims that a baseline always fails. The compact study comparisons retain
their sample counts and protocol context.

The two benchmark cards follow the interactive room, labeled Open space and With
wall layouts, and link to real Fable examples and their respective Leaderboards.
`home-leaderboard.js` fetches `paper_leaderboard.json` and displays the three
highest overall paper averages for the selected setting; no scores are hardcoded.
Why uses compact original scene crops next to HTML comparisons, with full Figure 1
panels linked. `paper-web-crops.json` records the vector crop coordinates and hashes.
How retains the original multi-view evidence sequence. Original visuals are used
for evidence and the revision curve, while task/flow diagrams use native HTML/SVG.
Results show human agreement with both baselines. Paper-only pipeline/agent
findings link to Tables 2 and 3; the excluded interactive boards stay excluded.
Revision emphasizes Figure 5a and the separate 0–1 study protocol. Layout-change
counts remain supplementary. The Table 9 record is explicitly a paper example,
not a substitute for the selected scene's missing report.

The illustrative room first presents three questions with principle tags only.
Its operation chips reflect actual illustration operations: A measures sofa/rug
bounding-box overlap and retrieves a stored view, B measures lamp/desk dimensions
and retrieves a stored view, and C inspects a stored view before requesting a new
front view and measuring clearance. Stored views are captured on scene setup or
placement changes. Door motion visualizes the measurement and is
explicitly distinguished from GREP simulating articulated furniture. The report-fix
action shows a staged generator revision, changing only the ottoman position.
Lamp scale stays unchanged in both states, and its diagnosis reflects that. The cabinet
narration remains an assistive live region, and diagnosis follows the measurement.

Astra WF S100–S109 and the absent Astra RC export have display-only `Coming soon`
states in Examples. WF placeholders yield to real scene rows automatically when
those IDs are imported; no scores or image paths are inferred for placeholders.
Missing current object attribution displays `Details coming soon`; historical
objects are not substituted. Existing object IDs receive readable presentation
labels while their exact IDs remain available in tooltips.

## Updating from the local authoring snapshot

`scripts/export_project_page.py` creates a fresh static export from the ignored
authoring workspace and captured display snapshot. It checks retained scores,
current object IDs, and the local asset dependency closure. Pass an empty output
directory with `--output`; `--website`, `--snapshot`, `--audit`, and
`--editable-output` are also supported. Raw experiment archives and credentials
are not part of the website.
