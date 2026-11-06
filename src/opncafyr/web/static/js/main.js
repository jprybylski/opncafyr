// Home page entry point (ADR 0006): plain ES modules, no build step, no third-party code.

import { initArtifacts } from "./artifacts.js";
import { initJobs } from "./jobs.js";
import { initLifecycle } from "./lifecycle.js";
import { initRouter } from "./router.js";
import { initTheme } from "./theme.js";
import { initTools } from "./tools.js";
import { initToolView } from "./toolview.js";
import { initWorkspace } from "./workspace.js";

initRouter();
initTheme();
initToolView();
initWorkspace();
initTools();
initArtifacts();
initJobs();
initLifecycle();
