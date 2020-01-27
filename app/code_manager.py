#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @File  : code_manager.py
# @Author: Kingtous
# @Date  : 2020-01-26
# @Desc  :
import os
import subprocess
import tempfile
import time
from threading import Semaphore, Thread

# 代码运行
import gevent

from app_config import PYTHON3_EXE, running_pool, C_EXE


class CodeBlock:

    def __init__(self, task_id, user_id):
        self.task_id = task_id
        self.user_id = user_id


# 代码运行状态
class CodeStatus:
    waiting = 0
    compiling = 1
    running = 2
    completed = 3
    # 错误
    error = 4
    error_file_not_exists = 5
    error_runtime_error = 6
    error_file_not_supported = 7
    error_compile_error = 8
    errors = [error, error_file_not_exists, error_runtime_error, error_file_not_supported, error_compile_error]


# 执行代码的协程，自动提交更改到数据库，并且可以通过getData得到result值
class CodeRunner:

    def __init__(self, code_block):
        self.code_block = code_block
        self.status = CodeStatus.waiting
        self.result = None
        self.is_got = False
        self.session = None

    def change_state(self, state):
        self.status = state
        if self.result is not None:
            self.result.status = state
            if self.session is not None:
                self.session.commit()

    def get_data(self):
        self.is_got = True
        return self.result

    def is_valid(self):
        return not self.is_got

    @staticmethod
    def run_command(command_list):
        return subprocess.Popen(command_list,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                shell=False).communicate()

    def run(self):
        from app_config import SQLSession
        session = SQLSession()
        self.session = session
        try:
            task_id = self.code_block.task_id
            user_id = self.code_block.user_id
            # 去数据库中查询
            from app.database_models import CodeResult
            result = session.query(CodeResult).filter_by(id=task_id, user_id=user_id).first()
            self.result = result
            if result is None:
                # 数据库中没有
                self.change_state(CodeStatus.error_file_not_exists)
                return False
            # result为CodeResult
            path = result.local_path
            # 判断后缀，执行不同语言的代码
            suffix = os.path.splitext(path)[1].lower()
            self.change_state(CodeStatus.running)
            if suffix == '.py':
                # 开始运行Python脚本
                p = subprocess.Popen([PYTHON3_EXE, path], stdout=subprocess.PIPE)
                p.wait()
                self.result.result = p.stdout.read()
            elif suffix == '.c':
                # C 语言
                with tempfile.TemporaryDirectory() as temp_dir:
                    t_path = os.path.join(temp_dir, 'c.c')
                    from app_utils import AppUtils
                    t_path = AppUtils.copy_file(path, t_path)
                    self.change_state(CodeStatus.compiling)
                    p = subprocess.Popen([C_EXE, t_path, '-o', os.path.join(temp_dir, 'c.out')],
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                         shell=False)
                    out, err = p.communicate()
                    exe_path = os.path.join(temp_dir, 'c.out')
                    if not os.path.exists(exe_path):
                        self.change_state(CodeStatus.error_compile_error)
                        self.result.result = err
                    else:
                        exe_out, exe_err = self.run_command([exe_path])
                        self.change_state(CodeStatus.completed)
                        self.result.result = exe_out
            elif suffix == '.cpp':
                # C 语言
                with tempfile.TemporaryDirectory() as temp_dir:
                    t_path = os.path.join(temp_dir, 'cpp.cpp')
                    from app_utils import AppUtils
                    t_path = AppUtils.copy_file(path, t_path)
                    self.change_state(CodeStatus.compiling)
                    p = subprocess.Popen([C_EXE, t_path, '-o', os.path.join(temp_dir, 'cpp.out')],
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                         shell=False)
                    out, err = p.communicate()
                    exe_path = os.path.join(temp_dir, 'cpp.out')
                    if not os.path.exists(exe_path):
                        self.change_state(CodeStatus.error_compile_error)
                        self.result.result = err
                    else:
                        exe_out, exe_err = self.run_command([exe_path])
                        self.change_state(CodeStatus.completed)
                        self.result.result = exe_out
            else:
                self.change_state(CodeStatus.error_file_not_supported)
            # 更新数据库
            self.change_state(CodeStatus.completed)
            print("CodeRunner: Execute %s Over." % path)
            print("Output:\n %s" % self.result.result)
            from app_utils import AppUtils
            AppUtils.update_sql(session)
        except Exception as e:
            print(e)
            self.change_state(CodeStatus.error)
        finally:
            from app_utils import AppUtils
            AppUtils.close_sql(session)


class CodeServerStatus:
    total = 0
    running = 0


# 每15秒更新一次数据，有多少正在运行
class CodeRunningDaemon(Thread):

    def __init__(self, sem, p_arr):
        super().__init__()
        self.p_arr = p_arr
        self.sem = sem

    def run(self):
        while True:
            self.sem.acquire()
            running_cnt = 0
            clean = []
            clean.clear()
            for status_tuple in self.p_arr:
                if status_tuple[0].status == CodeStatus.running:
                    running_cnt = running_cnt + 1
                    continue
                if status_tuple[0].status == CodeStatus.completed \
                        or status_tuple[0].status in CodeStatus.errors:
                    clean.append(status_tuple)
            # 清除死掉的协程
            for item in clean:
                self.p_arr.remove(item)
            total = len(self.p_arr)
            CodeServerStatus.total = total
            CodeServerStatus.running = running_cnt
            print("Stat：Total In Array：%d (greenlet)，Running：%d (greenlet)" % (
                CodeServerStatus.total, CodeServerStatus.running))
            self.sem.release()
            time.sleep(15)


class CodeManager:

    def __init__(self):
        self.process_array = []
        # 信号量
        self.queue_sem = Semaphore()
        # 启动获取统计数据的daemon线程
        daemon = CodeRunningDaemon(self.queue_sem, self.process_array)
        daemon.daemon = True
        daemon.start()

    def add_task(self, code_block):
        try:
            self.queue_sem.acquire()
            # 开始
            runner = CodeRunner(code_block)
            glet = running_pool.spawn(runner.run)
            self.process_array.append((runner, glet))
            gevent.sleep(0)
            # 结束
            self.queue_sem.release()
            return True
        except Exception as e:
            print(e)
            return False
