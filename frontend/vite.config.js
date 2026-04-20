import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import fs from "node:fs";
import path from "node:path";

/**
 * Vite 配置
 * base 适配 GitHub Pages 子路径部署（例如 username.github.io/DaLeTou/）
 * 通过环境变量 VITE_BASE 覆盖，本地开发默认 "/"
 *
 * 本地开发时，通过中间件把 /data/*.json 直接读自仓库根 data/export/，
 * 这样无需手动复制到 frontend/public/data 即可看到最新数据
 */
const dataExportDir = path.resolve(__dirname, "../data/export");

function serveDataExport() {
  return {
    name: "serve-data-export-in-dev",
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        if (!req.url) return next();
        const match = req.url.match(/^\/data\/([^?]+)/);
        if (!match) return next();
        const file = path.join(dataExportDir, match[1]);
        if (!file.startsWith(dataExportDir) || !fs.existsSync(file)) return next();
        const stat = fs.statSync(file);
        if (!stat.isFile()) return next();
        res.setHeader("Content-Type", "application/json; charset=utf-8");
        res.setHeader("Cache-Control", "no-store");
        fs.createReadStream(file).pipe(res);
      });
    },
  };
}

export default defineConfig({
  plugins: [vue(), serveDataExport()],
  base: process.env.VITE_BASE || "/",
  server: {
    port: 5173,
  },
});
