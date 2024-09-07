from multiprocessing.connection import Client

c = Client(('localhost', 25000), authkey=b'peekaboo')


c.send('stop')
print(c.recv())