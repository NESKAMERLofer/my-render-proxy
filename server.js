const http = require("http");
const net = require("net");
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
  if (req.url === "/ping") {
    res.writeHead(200, {
      "Content-Type": "text/plain; charset=utf-8"
    });
    res.end("pong");
    return;
  }

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

server.on("connect", (req, clientSocket, head) => {
  if (req.headers.authorization !== AUTH_TOKEN) {
    clientSocket.write(
      'HTTP/1.1 407 Proxy Authentication Required\r\nProxy-Authenticate: Basic realm="Render Proxy"\r\n\r\n'
    );
    clientSocket.destroy();
    return;
  }

  const [host, port = 443] = req.url.split(":");
  const serverSocket = net.connect(port, host, () => {
    clientSocket.write("HTTP/1.1 200 Connection Established\r\n\r\n");
    serverSocket.write(head);
    serverSocket.pipe(clientSocket);
    clientSocket.pipe(serverSocket);
  });

  serverSocket.on("error", () => {
    clientSocket.end("HTTP/1.1 502 Bad Gateway\r\n\r\n");
  });
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
