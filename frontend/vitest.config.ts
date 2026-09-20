import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

/** Minimal Vitest setup for Scenario Lab component tests (Gate C1 Slice 3). No
 * existing test infrastructure existed in this project before this file; see the
 * Slice 3 report's "Test Infrastructure" note for why this specific, minimal
 * addition (Vitest + React Testing Library, devDependencies only) was chosen over
 * either inventing bespoke assertions or a heavier E2E framework. */
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    globals: true,
  },
  resolve: {
    alias: {
      "@": __dirname,
    },
  },
});
