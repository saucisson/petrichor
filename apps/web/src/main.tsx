import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./app";
import "./styles.css";

const ROOT_ELEMENT = document.getElementById("root");

if (ROOT_ELEMENT === null) {
  throw new Error("No #root element to mount the application on.");
}

createRoot(ROOT_ELEMENT).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
