# coding:utf-8

from multiprocessing import Value, Queue, Process
import time
import logging

from api.apiServer import start_api_server
from db.DataStore import store_data, sqlhelper
from validator.Validator import validator, getMyIP
from spider.ProxyCrawl import startProxyCrawl
from tornadoproxy.proxy import start_random_proxy

from config import TASK_QUEUE_SIZE

logger = logging.getLogger()
logging.basicConfig(format='%(asctime)s - %(module)s:%(lineno)s - %(levelname)s: %(message)s',
                    level=logging.INFO)

if __name__ == "__main__":
    myip = getMyIP()
    DB_PROXY_NUM = Value('i', 0)
    CHECK_EXISTS_IP = Value('b', False)
    q1 = Queue(maxsize=TASK_QUEUE_SIZE)
    q2 = Queue()
    p1 = Process(target=startProxyCrawl, args=(q1, DB_PROXY_NUM, myip, CHECK_EXISTS_IP))
    p2 = Process(target=validator, args=(q1, q2, myip))
    p3 = Process(target=store_data, args=(q2, DB_PROXY_NUM))
    p1.start()
    p2.start()
    p3.start()

    while True:
        time.sleep(10)
        if CHECK_EXISTS_IP.value:
            if len(sqlhelper.select(count=10)) >= 10:
                p0 = Process(target=start_api_server)
                p0.start()

                p01 = Process(target=start_random_proxy)
                p01.start()

                break
            else:
                logger.info("IP池存量IP不足，爬虫正在抓取，等待10秒")
        else:
            logger.info("正在校验IP池存量IP的有效性，等待10秒")

    p0.join()
    p01.join()
    p1.join()
    p2.join()
    p3.join()
