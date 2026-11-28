import React from "react";
import { createRoot } from "react-dom/client";

import { Workbench } from "./Workbench.jsx";
import "./workbench.css";
import "./platform.css";

createRoot(document.getElementById("root")).render(<Workbench />);
