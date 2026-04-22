const http = require("http");
const httpProxy = require("http-proxy");

const PORT = process.env.PORT || 3000;
const AUTH_TOKEN = "Basic " + Buffer.from("user:password").toString("base64");

const proxy = httpProxy.createProxyServer({
  changeOrigin: true,
  secure: false
});

function requireAuth(req, res) {
  if (req.headers.authorization === AUTH_TOKEN) {
    return true;
  }

  res.writeHead(401, {
    "WWW-Authenticate": 'Basic realm="Render Proxy"',
    "Content-Type": "text/plain; charset=utf-8"
  });
  res.end("Authentication required");
  return false;
}

const server = http.createServer((req, res) => {
  if (!requireAuth(req, res)) {
    return;
  }

  let targetUrl;

  try {
    targetUrl = new URL(req.url);
  } catch (error) {
    res.writeHead(400, {
      "Content-Type": "text/plain; charset=utf-8"
    });
    res.end("Use a full URL, for example: http://example.com/");
    return;
  }

  const target = `${targetUrl.protocol}//${targetUrl.host}`;
  req.url = `${targetUrl.pathname}${targetUrl.search}`;

  proxy.web(req, res, { target });
});

proxy.on("error", (error, req, res) => {
  if (res.headersSent) {
    return;
  }

  res.writeHead(502, {
    "Content-Type": "text/plain; charset=utf-8"
  });
  res.end("Proxy error: " + error.message);
});

server.listen(PORT, () => {
  console.log(`Proxy server is running on port ${PORT}`);
});
