"""
Author: Chris Moyer <cmoyer@newstex.com>
SocketIO Basic functionality
"""

from botoweb.request import Request
from botoweb.response import Response
from botoweb.appserver.wsgi_layer import WSGILayer
from socketio import socketio_manage
from socketio.namespace import BaseNamespace
import logging
log = logging.getLogger('botoweb.appserver.socketio')

import json
import gevent
import urllib

class BWNamespace(BaseNamespace):
	"""Simple BotoWeb Namespace which routes requests directly
	to the application object"""
	def __init__(self, *args, **kwargs):
		super(BWNamespace, self).__init__(*args, **kwargs)
		self.headers = {}
		self.cache = {}

	def _request(self, method, args):
		"""Generic Request
		All request args must contain at least:
		:msg_id: The message ID to correspond back when returning a response
		:model: The name of the model corresponding to this request
			(this corresponds to the "name" attribtute in handlers.yaml)

		Requests may also contain:
		:id: ID of the object to request
		:params: Any GET/POST parameters to pass in. The proper parameters are always
			used determined by what request type this is
		:post: POST parameters to send (ignores request method)
		:get: GET parameters to send (ignores request method)
		"""
		try:
			# Sanity Checking
			if not args.has_key('msg_id'):
				self.emit('err', {'code': 400, 'msg': 'No msg_id provided'})
				return
			if not args.has_key('model'):
				self.emit('err', {'code': 400, 'msg': 'No model provided'})
				return

			path = self.request['routes'].get(args['model'], args['model'])
			log.info('%s: %s => %s' % (method, args['model'], path))

			# Add in any GET/POST parameters
			post_params = None
			get_params = None
			if method == 'POST':
				post_params = args.get('params')
			else:
				get_params = args.get('params')
			if args.has_key('get'):
				get_params = args.get('get')
			if args.has_key('post'):
				post_params = args.get('post')

			# Set up the Request and Response
			# objects
			resp = Response()
			environ = self.environ.copy()
			environ['REQUEST_METHOD'] = method.upper()
			# If an ID is specified, add that to the path
			if args.has_key('id'):
				path += '/' + args['id']
			# Also allow a parameter
			if args.has_key('param'):
				path += '/' + args['param']

			# Set the path
			environ['PATH_INFO'] = path
			# Add in any GET paramters
			if get_params:
				environ['QUERY_STRING'] = urllib.urlencode(get_params)

			# Set up authorization
			if self.request.has_key('AUTH'):
				username = self.request['AUTH'].get('username')
				password = self.request['AUTH'].get('password')
				auth_header = ':'.join([username, password])
				auth_header = 'BASIC %s' % auth_header.encode('base64')
				environ['HTTP_AUTHORIZATION'] = auth_header
			req = Request(environ)
			req.accept = self.headers.get('accept', req.headers.get('X-Application-Accept', 'application/json'))
			# Add in any POST params
			if post_params:
				req.content_type = 'application/x-www-form-urlencoded'
				req.body = urllib.urlencode(post_params)
				
			for header in self.headers:
				# We already handled the accept header above
				if header == 'accept':
					continue
				req.headers[header] = self.headers[header]

			# Add in any cached items
			req.cache = self.cache
			if self.cache.has_key('user'):
				req._user = self.cache['user']

			# Execute the application
			try:
				self.request['app'].handle(req, resp)
				if 'json' in resp.content_type:
					for line in resp.app_iter:
						if line:
							data = json.loads(line)
							self.emit('data', {'msg_id': args['msg_id'], 'msg': data})
				else:
					self.emit('data', {'msg_id': args['msg_id'], 'msg': resp.body})
			except Exception:
				log.exception('Error processing: %s' % args)

			# Handle any caching
			if req.user:
				self.cache['user'] = req.user
			for item in req.cache:
				self.cache[item] = req.cache[item]
		except Exception:
			log.exception('Error processing: %s' % args)

	# Allow custom headers
	def on_HEADER(self, headers):
		for header in headers:
			log.info('HEADER %s = %s' % (header.lower(), headers[header]))
			self.headers[header.lower()] = headers[header]

	# Handle Authentication
	def on_AUTH(self, args):
		"""Auth just sends username/password, which we
		then just build into our request object"""
		self.request['AUTH'] = args

	def on_GET(self, args):
		"""GET request"""
		gevent.spawn(self._request, 'GET', args)

	def on_POST(self, args):
		gevent.spawn(self._request, 'POST', args)

	def on_PUT(self, args):
		gevent.spawn(self._request, 'PUT', args)

	def on_DELETE(self, args):
		gevent.spawn(self._request, 'DELETE', args)

class SocketIOLayer(WSGILayer):
	"""SocketIO WSGI Layer.
	This routes all requests to the application
	but captures socket.io requests routing them
	through the SocketIONamespace
	"""
	namespace_class = BWNamespace

	def update(self, env):
		super(SocketIOLayer, self).update(env)
		# Bootstrap the handlers
		self.routes = {}
		for route in self.env.config.get('botoweb', 'handlers'):
			if route.get('name'):
				self.routes[route['name']] = route['url'].strip('$')

	def __call__(self, environ, start_response):
		"""Intercept Socket.IO requests"""
		path = environ['PATH_INFO'].strip('/')
		if path.startswith('socket.io'):
			socketio_manage(environ, {'':BWNamespace}, request={'app': self.app, 'routes': self.routes})
		else:
			# All other requests get routed to the application
			return self.app(environ, start_response)
