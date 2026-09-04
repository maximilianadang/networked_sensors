---
name: physical-wiring-layout
description: Create or revise single-page, photo-based hardware wiring layouts that show physical boards, connectors, terminals, and routed wires rather than circuit schematics. Use for installation sheets, harness maps, controller migrations, and visual wiring documentation where exact terminals and readable physical placement matter.
---

# Physical Wiring Layout

Produce an installation-facing physical layout: someone should be able to identify the pictured hardware, locate each real terminal, and wire it without translating from a circuit schematic.

## Follow this workflow in order

Do not begin routing wires until the hardware photographs and terminal map pass the first four steps.

1. **Inventory the exact hardware.** Identify the model, revision, connector orientation, installed cable gender, and which connections are new, existing, unused, or internal modifications. Inspect the user's reference layout and any current implementation before composing.
2. **Write the wiring contract.** Make a conductor-by-conductor table containing both exact endpoints, signal role, voltage domain, polarity, color, and verification source. Reconcile the table with firmware pin assignments and user-confirmed wiring.
3. **Acquire terminal-readable photographs.** Use real photographs of the exact model and revision. Open each candidate at native resolution and confirm that every terminal hole and relevant silkscreen label can be distinguished. Replace a blurry image before drawing; do not compensate for unreadable photography with guessed coordinates or synthetic connector boxes.
4. **Calibrate every connector.** For each photograph, make a terminal map in the photograph's actual orientation. Count the complete connector sequence from a physically identifiable end, including unused pins before, between, and after the connected pins. Cross-check the sequence against an authoritative pinout. Record the center of each connected screw, socket, pad, or connector orifice in source-image coordinates, then transform those coordinates into page coordinates using the image's actual rendered scale, crop, and letterboxing.
5. **Place photographs and labels.** Arrange the real hardware on a plain background with generous routing corridors. Keep names, notes, and pin tags outside photographs and away from silkscreen. Ensure connectors remain large enough to inspect in the final-resolution sheet.
6. **Route wires to calibrated centers.** Terminate every path inside the visible physical orifice represented by its recorded coordinate. Route from the terminal outward so neighboring endpoints cannot split the difference between two holes. Separate unlike colors, text, and silkscreen throughout the route.
7. **Render the actual deliverable.** Render the HTML at the intended fixed viewport. Treat the PNG—not the HTML, DOM, or coordinate list—as the result under review.
8. **Perform connector-by-connector inspection.** Create a high-resolution crop around every connected component, enlarged enough to see individual holes and printed labels. For every conductor, verify its endpoint is centered in the intended orifice and not merely touching the connector body. Verify the printed terminal identifier independently rather than assuming the coordinate map was correct.
9. **Correct and rerender in a loop.** If any endpoint falls between holes, enters the wrong hole, ends outside the connector, obscures silkscreen, or cannot be judged confidently, fix it and repeat steps 7–8 for the affected component. Changing the photograph invalidates all coordinates on that photograph and requires recalibration.
10. **Audit the whole sheet.** Inspect the full-resolution page for wire crossings, shared unlike-colored paths, labels over hardware, unreadable text, clipping, inconsistent legends, and discrepancies between the connection table and drawn routes.
11. **Report completion only after the final render passes.** Summarize which connector crops were inspected and identify any endpoint that remains unverified. Never call the sheet complete from plausible coordinates, successful rendering, or partial component review.

## Establish the wiring contract first

Before drawing, build a connection table from authoritative documentation, inspected source code, verified measurements, and user-confirmed installed wiring. For every conductor record:

- source component and exact terminal;
- destination component and exact terminal;
- signal purpose, voltage domain, polarity, and wire color;
- whether it is new, existing/unchanged, unused, or requires an internal modification.

Resolve contradictions before presenting a connection as fact. Do not infer pin order from connector shape, wire color, a similar board revision, or USB conventions. Explicitly identify unverified assumptions and do not depict them as installation-ready.

Check electrical compatibility as well as logical equivalence. Distinguish MCU-level headers, conditioned industrial inputs, high-side outputs, open-collector/sinking interfaces, relay contacts, power rails, and protective earth. A visually clear but electrically invalid route is a failed deliverable.

## Use the established visual language

When working in this repository, read [references/project-reference.md](references/project-reference.md) and inspect its named reference image and current HTML implementation before editing or creating a sheet.

For other projects, inspect any user-supplied reference before choosing a layout. Preserve the user's presentation conventions unless they conflict with electrical accuracy or legibility.

Use real photographs of the actual model/revision wherever practical. Prefer manufacturer or distributor images with a clear terminal view. Reuse repository assets only when they pass the terminal-readable photograph gate above; otherwise find a sharper exact-model image. Crop or isolate hardware only when it improves terminal visibility without changing orientation or hiding relevant context.

The default deliverable is:

- one fixed-size HTML page using positioned photographs, labels, and SVG wire paths;
- one rendered PNG at the same aspect ratio and resolution;
- a compact connection summary and color key on the page.

Do not substitute conventional circuit symbols or an abstract block diagram unless the user asks for one.

## Compose for physical readability

- Place hardware first, leaving dedicated routing corridors and label zones.
- Keep component names and explanatory labels outside photographs unless a small pin tag must align with a terminal.
- Put pin tags next to, not over, the printed pin number or terminal marking.
- Terminate every wire at a visible landing marker centered on the photographed terminal.
- A wire endpoint must enter one unambiguous screw recess, socket hole, solder pad, or connector orifice. Ending on a connector outline, between adjacent holes, or on an inferred bounding box fails the layout.
- Route wires orthogonally or with restrained bends. Keep parallel wires separated consistently.
- Do not allow differently colored conductors to share the same path. Avoid crossings; if one is unavoidable, reroute first and use an unmistakable visual separation only as a last resort.
- Keep wires away from pin numbers, terminal text, component names, notes, and other labels.
- Use one continuous ground network where electrically correct; do not draw redundant ground jumpers merely to make connectivity look explicit.
- Assign colors by signal role and use them consistently in paths, landing markers, pin tags, connection text, and legend.
- Mark unused terminals clearly without suggesting that they are connected.
- Separate unchanged field wiring from new wiring so installation scope is obvious.

## Render and inspect, do not trust coordinates

Render the HTML to PNG at the intended viewport after every meaningful routing pass. Inspect the actual bitmap at full-page scale and in a separate enlarged crop around every connected component. Inspect source photographs at native resolution when terminal identity is uncertain.

The sheet is not complete until all of these hold:

- every component and connector is the correct physical item and orientation;
- every wire lands on the intended visible terminal;
- every connected terminal has been checked in a crop from the final render, not an earlier render;
- the complete physical pin sequence was counted so unused neighboring sockets cannot shift the mapping;
- terminal names and pin numbers remain readable;
- no label overlaps hardware unintentionally;
- no wire obscures a label or pin marking;
- no unlike-colored wires overlap or become visually ambiguous;
- the legend matches every used color and omits misleading entries;
- connection-summary text agrees exactly with the drawn paths;
- warnings and supply-removal instructions are visible where the work involves hazardous energy or irreversible modification;
- the PNG contains the entire sheet with no clipping or browser chrome.

If visual inspection finds a collision or ambiguous landing, fix the geometry and render again. Continue until all final connector crops pass. Do not claim completion based only on valid HTML, plausible SVG coordinates, a correct-looking full-page preview, or one successfully repaired component.

## Preserve maintainability

Keep dimensions, colors, component positions, pin-tag positions, and wire routes explicit and locally understandable in the HTML/CSS/SVG. Use descriptive classes based on signal roles. Keep source/reference attribution unobtrusive on the sheet. Preserve unrelated assets and existing user changes.
