---
title: Tutorials
parent: Guides
nav_order: 7.7
description: Walkthroughs that point at each part of the app while you do the work, and how to write your own.
permalink: /guides/tutorials/
---

# Tutorials

Walkthroughs that point at each part of the app while you do the work yourself.
{: .fs-5 .fw-300 }

{: .research }
> **Research use only.** A tutorial teaches the screens. It never acts for you, and finishing one does not validate an analysis.

A tutorial shows a small panel with directions. It puts a ring around the control to use, opens the right page, and waits for you to do the step. It never clicks, chooses, runs or publishes anything.

## Take a tutorial

1. Select **Tutorials** on the left, then **Start** beside **Your first analysis**.
2. Do each step on the page, then select **Next**. Some steps wait until the page shows the step as done, such as a file being imported. **Skip** always moves on.
3. Use **Back**, **Pause** or **Exit tutorial** at any time. **Escape** closes the panel too.

{: .expect }
Your place is saved by the app, so a reload or the next launch brings the tutorial back where you left it. **Reset progress** forgets it.

{: .fix }
If the panel says a control isn't on the page right now, it may appear after an earlier step (for example, **Import** shows after a preview). Select **Look again**, or **Continue without highlight** to keep the directions without the ring.

Inside an app with its own screens, such as opnca-r, a tutorial gives directions in the panel; it can't point at that app's controls.

## Write your own

Select **Copy into this study** beside an example. The copy is saved as `tutorials/<name>.yaml` in the study folder, where you can edit it in any text editor. Each step has a title and plain text. A step can also name a page to show, a control to highlight and a condition to wait for, chosen from the names the app lists. A tutorial can't hold HTML, scripts, web addresses, file paths or commands. The in-app **Guide** has the full format under **Tutorials**.
