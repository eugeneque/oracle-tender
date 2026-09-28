import React from "react";
import ReactDOM from "react-dom/client";
import { MotionConfig } from "motion/react";
import { BrowserRouter } from "react-router-dom";

import App from "./App";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      {/* Анимации смягчаются до простых смен прозрачности, если в системе включено
          «Уменьшить движение». */}
      <MotionConfig reducedMotion="user">
        <App />
      </MotionConfig>
    </BrowserRouter>
  </React.StrictMode>,
);
