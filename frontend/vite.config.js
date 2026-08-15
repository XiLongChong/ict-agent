import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig(({ command }) => ({
  plugins: [vue()],
  // dev 时 base 为 /，构建时保持 /static/ 供 FastAPI 的 StaticFiles 正确引用
  base: command === "serve" ? "/" : "/static/",
  build: { outDir: "dist", emptyOutDir: true },
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
}));
