#!/usr/bin/env python
#
# Simple asynchronous HTTP proxy with tunnelling (CONNECT).
#
# GET/POST proxying based on
# http://groups.google.com/group/python-tornado/msg/7bea08e7a049cf26
#
# Copyright (C) 2012 Senko Rasic <senko.rasic@dobarkod.hr>
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

import logging
import re
import socket
import config

import tornado.httpserver
import tornado.ioloop
import tornado.iostream
import tornado.web
import tornado.httpclient
import tornado.netutil


logger = logging.getLogger()
logging.basicConfig(format='%(asctime)s - %(module)s:%(lineno)s - %(levelname)s: %(message)s',
                    level=logging.INFO)


class ProxyHandler(tornado.web.RequestHandler):
    SUPPORTED_METHODS = ['GET', 'POST', 'CONNECT']

    @tornado.web.asynchronous
    def get(self):
        logger.info('Handle %s request to ---> %s', self.request.method, self.request.uri)

        def handle_response(response):
            # self.request.headers.get("X-Real-Ip",'')
            if (response.error and not
                    isinstance(response.error, tornado.httpclient.HTTPError)):
                self.set_status(500)
                self.write('Internal server error:\n' + str(response.error))
            else:
                self.set_status(response.code)
                for header in ('Date', 'Cache-Control', 'Server', 'Content-Type', 'Location'):
                    v = response.headers.get(header)
                    if v:
                        self.set_header(header, v)
                v = response.headers.get_list('Set-Cookie')
                if v:
                    for i in v:
                        self.add_header('Set-Cookie', i)
                self.add_header('VIA', 'Toproxy')
                if response.body:
                    self.write(response.body)
            self.finish()

        if base_auth_user:
            auth_header = self.request.headers.get('Authorization', '')
            if not base_auth_valid(auth_header):
                self.set_status(403)
                self.write('Auth Faild')
                self.finish()
                return

        user_agent = self.request.headers.get('User-Agent', '')
        if shield_attack(user_agent):
            self.set_status(500)
            self.write('nima')
            self.finish()
            return

        client_ip = self.request.remote_ip
        if not match_white_iplist(client_ip):
            logger.debug('deny %s', client_ip)
            self.set_status(403)
            self.write('')
            self.finish()
            return
        body = self.request.body
        if not body:
            body = None
        try:
            fetch_request(
                self.request.uri, handle_response,
                method=self.request.method, body=body,
                headers=self.request.headers, follow_redirects=False,
                allow_nonstandard_methods=True)
        except tornado.httpclient.HTTPError as e:
            if hasattr(e, 'response') and e.response:
                handle_response(e.response)
            else:
                self.set_status(500)
                self.write('Internal server error:\n' + str(e))
                self.finish()

    @tornado.web.asynchronous
    def post(self):
        return self.get()

    @tornado.web.asynchronous
    def connect(self):
        logger.info('Start CONNECT to ---> %s', self.request.uri)
        client = self.request.connection.stream

        def read_from_client(data):
            upstream.write(data)

        def read_from_upstream(data):
            client.write(data)

        def client_close(data=None):
            if upstream.closed():
                return
            if data:
                upstream.write(data)
            upstream.close()

        def upstream_close(data=None):
            if client.closed():
                return
            if data:
                client.write(data)
            client.close()

        def start_tunnel():
            logger.debug('CONNECT tunnel established to %s', self.request.uri)
            client.read_until_close(client_close, read_from_client)
            upstream.read_until_close(upstream_close, read_from_upstream)
            if not client.closed():
                client.write(b'HTTP/1.0 200 Connection established\r\n\r\n')

        def on_proxy_response(data=None):
            if data:
                first_line = data.splitlines()[0]
                http_v, status, text = first_line.split(None, 2)
                if int(status) == 200:
                    logger.debug('Connected to upstream proxy %s', proxy)
                    start_tunnel()
                    return

            self.set_status(500)
            self.finish()

        def start_proxy_tunnel():
            # upstream.write('Server: Toproxy\r\n')
            upstream.write(('CONNECT %s HTTP/1.1\r\n' % self.request.uri).encode("utf-8"))
            upstream.write(('Host: %s\r\n' % self.request.uri).encode("utf-8"))
            upstream.write('Proxy-Connection: Keep-Alive\r\n\r\n'.encode("utf-8"))
            upstream.read_until(b'\r\n\r\n', on_proxy_response)

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM, 0)
        upstream = tornado.iostream.IOStream(s)

        proxy = get_proxy()
        if proxy:
            upstream.connect((proxy["host"], proxy["port"]), start_proxy_tunnel)
        else:
            host, port = self.request.uri.split(':')
            upstream.connect((host, int(port)), start_tunnel)


def get_proxy():
    return None
    # return {
    #     "host": "218.60.8.99",
    #     "port": 3129
    # }


def base_auth_valid(auth_header):
    auth_mode, auth_base64 = auth_header.split(' ', 1)
    assert auth_mode == 'Basic'
    auth_username, auth_password = auth_base64.decode('base64').split(':', 1)
    if auth_username == base_auth_user and auth_password == base_auth_passwd:
        return True
    else:
        return False


def match_white_iplist(clientip):
    if clientip in white_iplist:
        return True
    if not white_iplist:
        return True
    return False


def shield_attack(header):
    if re.search(header, 'ApacheBench'):
        return True
    return False


def fetch_request(url, callback, **kwargs):
    proxy = get_proxy()
    if proxy:
        logger.debug('Forward request via upstream proxy %s', proxy)
        tornado.httpclient.AsyncHTTPClient.configure(
            'tornado.curl_httpclient.CurlAsyncHTTPClient')
        kwargs['proxy_host'] = proxy["host"]
        kwargs['proxy_port'] = proxy["port"]

    # req = tornado.httpclient.HTTPRequest(url, **kwargs)
    client = tornado.httpclient.AsyncHTTPClient()
    client.fetch(url, callback, follow_redirects=True, max_redirects=3)


def run_proxy(port, pnum=1):
    import tornado.process
    app = tornado.web.Application([
        (r'.*', ProxyHandler),
    ])

    if pnum > 200 or pnum < 0:
        raise ValueError("process num is too big or small")
    if pnum == 1:
        app.listen(port)
        tornado.ioloop.IOLoop.instance().start()
    else:
        sockets = tornado.netutil.bind_sockets(port)
        tornado.process.fork_processes(pnum)
        server = tornado.httpserver.HTTPServer(app)
        server.add_sockets(sockets)
        tornado.ioloop.IOLoop.instance().start()


def _start_random_proxy(port=8888, white=None, user=None, fork=1):
    global white_iplist, base_auth_user, base_auth_passwd
    white_iplist = white or []

    if user:
        base_auth_user, base_auth_passwd = user.split(':')
    else:
        base_auth_user, base_auth_passwd = None, None

    # print("Starting HTTP proxy on port %d" % port)
    logger.info(">>>>>> 随机代理服务器启动！端口：%d" % port)
    run_proxy(port, fork)


def start_random_proxy():
    _start_random_proxy(
        port=int(config.RANDOM_PROXY_PORT),
        white=config.RANDOM_PROXY_WHITE,
        user=config.RANDOM_PROXY_USER,
        fork=int(config.RANDOM_PROXY_FORK)
    )


white_iplist = []

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='''python -m toproxy/proxy  -p 8888 -w 127.0.0.1,8.8.8.8 -u xiaorui:fengyun''')

    parser.add_argument('-p', '--port', help='tonado proxy listen port', action='store', default=8888)
    parser.add_argument('-w', '--white', help='white ip list ---> 127.0.0.1,215.8.1.3', action='store', default=[])
    parser.add_argument('-u', '--user', help='Base Auth , xiaoming:123123', action='store', default=None)
    parser.add_argument('-f', '--fork', help='fork process to support', action='store', default=1)
    args = parser.parse_args()

    if not args.port:
        parser.print_help()

    _start_random_proxy(port=int(args.port), white=args.white, user=args.user, fork=int(args.fork))

