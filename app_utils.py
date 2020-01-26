import os, sys

import pyfiglet
from flask import jsonify
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, scoped_session
from wtforms import ValidationError

import app_config as Cf
from api.response_code import ResponseCode
from app.code_manager import CodeManager


class AppUtils:

    @staticmethod
    def init(app):
        # APP Server Banner
        print(pyfiglet.figlet_format("Kingtous Kits"))
        print("Code Running Server By Kingtous")
        # 初始化secret_key
        app.config['SECRET_KEY'] = Cf.secret_key
        # 防范CSRF攻击
        app.config["CSRF_ENABLED"] = True
        # 初始化数据库
        app.config['SQLALCHEMY_DATABASE_URI'] = Cf.base_mysql_connection_url
        app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = True
        app.config['SQLALCHEMY_COMMIT_TEARDOWN'] = True
        app.config['USER_ENABLE_EMAIL'] = False
        db = SQLAlchemy(app)
        Cf.database = db
        Cf.SQLBase = declarative_base()
        Cf.SQLEngine = create_engine(Cf.base_mysql_connection_url)
        Cf.SQLSessionMaker = sessionmaker(bind=Cf.SQLEngine)
        Cf.SQLSession = scoped_session(Cf.SQLSessionMaker)  # scoped_session保证线程安全
        # 必须import database_models初始化数据库各类!
        import database_models
        Cf.database.create_all()

        # 未登录回调
        @Cf.auth.error_handler
        def unauthorized():
            return jsonify(code=ResponseCode.LOGIN_REQUIRED)

        # 启动代码运行服务
        Cf.code_manager = CodeManager()

    @staticmethod
    def get_network_url(local_url):
        return local_url.replace(os.path.dirname(__file__), Cf.base_url)

    @staticmethod
    def get_local_path(network_url):
        return network_url.replace(Cf.base_url, os.path.dirname(__file__))

    @staticmethod
    def validate_username(username):
        from database_models import User
        user = User.query.filter_by(username=username).first()
        if user is not None:
            raise ValidationError('Please use a different username.')

    @staticmethod
    def validate_email(email):
        from database_models import User
        user = User.query.filter_by(email=email).first()
        if user is not None:
            raise ValidationError('Please use a different email address.')

    @staticmethod
    def add_to_sql(data):
        session = Cf.SQLSession()
        session.add(data)
        session.commit()
        return session

    @staticmethod
    def update_sql(session):
        session.commit()

    @staticmethod
    def close_sql(session):
        session.close()