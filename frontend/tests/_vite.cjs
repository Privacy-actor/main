const path = require('node:path')

/** 用项目自己的 Vite 管线加载 TS/TSX 模块，不监听端口。 */
async function loader(name) {
  const { createServer } = await import('vite')
  const server = await createServer({
    root: path.resolve(__dirname, '..'),
    cacheDir: path.resolve(__dirname, `../node_modules/.vite-tests/${name}`),
    server: { middlewareMode: true, ws: false, watch: null, hmr: false },
    appType: 'custom', logLevel: 'error',
  })
  return { server, load: modulePath => server.ssrLoadModule(modulePath) }
}

module.exports = { loader }
