import React from "react";
import ReactDOM from "react-dom/client";
import { Toaster } from "sonner";
import App from "@/App";
import "highlight.js/styles/github-dark.css";
import "@/index.css";

const savedTheme = (() => {
  try {
    return localStorage.getItem("nemesis-theme");
  } catch {
    return null;
  }
})();
if (savedTheme === "dark") {
  document.documentElement.classList.add("dark");
} else {
  document.documentElement.classList.remove("dark");
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
    <Toaster
      theme={savedTheme === "dark" ? "dark" : "light"}
      position="bottom-right"
      richColors
      closeButton
    />
  </React.StrictMode>,
);
