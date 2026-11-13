// Home page entry point (ADR 0006): plain ES modules, no build step, no third-party code.

import { initArtifacts } from "./artifacts.js";
import { initDatasets } from "./datasets.js";
import { initEditor } from "./editor.js";
import { initGuide } from "./guide.js";
import { initJobs } from "./jobs.js";
import { initLifecycle } from "./lifecycle.js";
import { initRouter } from "./router.js";
import { initSupport } from "./support.js";
import { initTheme } from "./theme.js";
import { initTools } from "./tools.js";
import { initTutorials } from "./tutorial.js";
import { initToolView } from "./toolview.js";
import { initWorkflows } from "./workflows.js";
import { initWorkspace } from "./workspace.js";

initRouter();
initTheme();
initToolView();
initEditor();
initGuide();
initWorkspace();
initTools();
initArtifacts();
initDatasets();
initJobs();
initLifecycle();
initWorkflows();
initSupport();
initTutorials();
