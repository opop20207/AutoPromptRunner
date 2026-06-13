import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Minimal Vite config. The API base URL is configured at runtime via the
// VITE_API_BASE_URL env var (see src/api/client.ts), not here.
export default defineConfig({
  plugins: [react()],
});
