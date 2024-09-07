from multiprocessing.connection import Listener
from multiprocessing import Process, Value

import traceback
import time
import os, signal

def echo_client(conn, flag):
    try:
        while True:
            msg = conn.recv()
            conn.send(msg)
            if msg == 'stop':
                flag.value = True

    except EOFError:
        print('Connection closed')

def echo_server(address, authkey, flag):
    serv = Listener(address, authkey=authkey)
    while True:
        try:
            client = serv.accept()
            echo_client(client, flag)
        except Exception:
            traceback.print_exc()

if __name__ == "__main__":
    flag = Value('i', False)
    print(flag.value)
    p = Process(target=echo_server, args=(('', 25000),b'peekaboo', flag))
    p.start()
    #p.join()
    while(not flag.value):
        print('------------------------------')

        print(flag.value)

        print("helloworld")
        time.sleep(1)
    os.kill(p.pid, signal.SIGTERM)