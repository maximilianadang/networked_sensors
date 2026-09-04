# Terraforming Mars wiring-layout reference

Use this reference only for wiring-layout work in `networked_sensors`.

## Style source

Inspect both files before composing or revising a diagram:

- `documentation/wiring/image.png` — the user's presentation reference: real product photographs, large whitespace, direct physical wire routes, and a compact color key.
- `documentation/wiring/yun-dro-stepper-wiring.html` — the maintained implementation of that style using a fixed canvas, positioned photo assets, external labels, pin tags, SVG paths, landing markers, and a rendered-output workflow.

Inspect the current rendered output too:

- `documentation/wiring/yun-dro-stepper-wiring.png`

The rendered PNG is the visual truth. The HTML is the editable source, not evidence that the layout is readable.

## Project-specific lessons

Previous review established these requirements:

- labels must not overlap or become unreadable against photographs;
- differently colored wires must not overlap;
- wire paths must not obscure pin numbers;
- component photos must correspond to the actual parts in hand;
- connector terminal order must reflect the observed physical device, not a generic connector convention;
- redundant ground paths should be removed when a single common connection is electrically sufficient;
- internal modifications, existing wiring, and new wiring must be visually distinguished;
- a connection table and drawing must say the same thing.

The Yún wiring revision exposed an additional mandatory lesson: never derive header positions by starting at the first pin of interest. Count the entire physical header from an identifiable end, including unused leading pins. A blurry Yún image caused SCL, SDA, AREF, and GND to be skipped before D13, shifting several otherwise plausible wire landings. Use a terminal-readable exact-revision photograph plus the authoritative pinout, then verify each landing in an enlarged crop of the final PNG.

For each connected component, the review crop must show all of the following at once:

- the physical hole, screw recess, pad, or connector orifice;
- the wire endpoint entering its center;
- the nearby silkscreen or another unambiguous orientation reference.

If a crop cannot show those facts clearly, obtain a better source photograph or enlarge/reposition the component. Do not add synthetic terminal boxes as a substitute for an unreadable photograph.

The existing sheet uses a white SVG halo at crossings. Treat that as a last-resort separation technique, not permission to create avoidable crossings or shared routes.

## Repository conventions

Store page sources, rendered sheets, and acquired component photos under `documentation/wiring/`. Reuse `documentation/wiring/assets/` when the exact hardware image already exists. Use descriptive asset names rather than retailer download names.

Render a fixed viewport matching the page dimensions and inspect with the local image viewer. Keep the HTML and PNG together so later hardware migrations can revise the source and compare the result.
