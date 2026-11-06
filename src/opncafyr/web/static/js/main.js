// Home page entry point (ADR 0006): plain ES modules, no build step, no third-party code.

import { initArtifacts } from "./artifacts.js";
import { initJobs } from "./jobs.js";
import { initLifecycle } from "./lifecycle.js";
import { initTools } from "./tools.js";
import { initWorkspace } from "./workspace.js";

initWorkspace();
initTools();
initArtifacts();
initJobs();
initLifecycle();
