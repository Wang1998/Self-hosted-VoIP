import os
import time
import logging
import subprocess
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters
from telegram import Update
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import requests
# pip install python-telegram-bot==13.7 watchdog
# 
# 配置
TOKEN = "Token"  # 替换为你的Bot Token
MESSAGE_DIR = "/var/log/asterisk/unread_sms/"  # 替换为你的消息文件夹路径
ALLOWED_IDS = [id]  # 替换为允许的Telegram用户ID列表

# 代理配置（根据你的代理类型选择一种）
PROXY = {
    # HTTP代理示例
    # 'proxy_url': 'http://proxy.example.com:8080',
    # 如果需要认证：
    # 'proxy_url': 'http://username:password@proxy.example.com:8080',
    #'proxy_url': 'http://username:password@proxy.example.com:8080'
    # SOCKS5代理示例
    #'proxy_url': 'socks5://proxy.example.com:1080',
    # 如果需要认证：
    # 'proxy_url': 'socks5://username:password@proxy.example.com:1080',
}
# ===== Bark 推送配置 =====
# 在这里填入你的 Bark device key 列表（可以有多个），例如 ["device_key1", "device_key2"]
BARK_KEYS = ["key"]
# 如果你使用自建 Bark 服务，修改为你的服务地址（不要以 '/' 结尾），例如 "https://bark.yourdomain.com"
BARK_SERVER = "http://push.liankong.vip"  #  http://push.liankong.vip/key/
# Bark 超时和重试设置
BARK_TIMEOUT = 5
BARK_MAX_RETRIES = 3
BARK_RETRY_DELAY = 1


# 日志配置
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class MessageHandler(FileSystemEventHandler):
    """文件夹变化处理器"""
    def __init__(self, bot, allowed_ids):
        self.bot = bot
        self.allowed_ids = allowed_ids

    def on_created(self, event):
        """当新文件创建时触发"""
        if not event.is_directory and event.src_path.endswith('.txt'):
            logger.info(f"检测到新文件: {event.src_path}")
            time.sleep(0.5)  # 确保文件写入完成
            try:
                if not os.path.exists(event.src_path):
                    logger.error(f"文件 {event.src_path} 不存在")
                    return
                if os.path.getsize(event.src_path) == 0:
                    logger.warning(f"文件 {event.src_path} 为空")
                    for chat_id in self.allowed_ids:
                        self.bot.send_message(chat_id=chat_id, text=f"New message: 文件 {os.path.basename(event.src_path)} 为空")
                    os.remove(event.src_path)
                    return

                with open(event.src_path, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                logger.info(f"读取到文件内容: {content}")

                if not content:
                    logger.warning(f"文件 {event.src_path} 内容为空")
                    for chat_id in self.allowed_ids:
                        self.bot.send_message(chat_id=chat_id, text=f"New message: 文件 {os.path.basename(event.src_path)} 内容为空")
                    # Bark 推送：文件为空
                    send_bark(f"New message: {os.path.basename(event.src_path)}", "文件内容为空")
                else:
                    for chat_id in self.allowed_ids:
                        self.bot.send_message(chat_id=chat_id, text=f"New message:\n{content}")
                        logger.info(f"消息已发送给用户 {chat_id}")
                    # Bark 推送：正常内容
                    # title 简短，body 写完整内容
                    send_bark(f"New SMS: {os.path.basename(event.src_path)}", content)

                os.remove(event.src_path)
                logger.info(f"删除文件: {event.src_path}")
            except Exception as e:
                logger.error(f"处理文件 {event.src_path} 失败: {str(e)}")
                for chat_id in self.allowed_ids:
                    self.bot.send_message(chat_id=chat_id, text=f"读取 {os.path.basename(event.src_path)} 失败: {str(e)}")

# BARK推送
def send_bark(title: str, body: str, keys=None):
    """
    自动美化 New SMS: filename 标题，并推送 Bark
    """
    if keys is None:
        keys = BARK_KEYS

    if not keys:
        logger.debug("未配置 BARK_KEYS，跳过 Bark 推送")
        return

    # 尝试自动解析 title 中的 filename
    # 你的 title 传入格式:  "New SMS: 20251207173900-+8618758003245.txt"
    sms_time = ""
    sms_phone = ""

    try:
        if title.startswith("New SMS:"):
            filename = title.replace("New SMS:", "").strip()
            base = filename.replace(".txt", "")

            parts = base.split("-", 1)
            if len(parts) == 2:
                raw_time, sms_phone = parts

                # 解析 YYYYMMDDHHMMSS
                if len(raw_time) == 14 and raw_time.isdigit():
                    sms_time = f"{raw_time[0:4]}-{raw_time[4:6]}-{raw_time[6:8]} " \
                               f"{raw_time[8:10]}:{raw_time[10:12]}:{raw_time[12:14]}"

        # 根据解析结果生成美化标题
        if sms_phone and sms_time:
            title = f"📩 新短信来自 {sms_phone}（{sms_time}）"
        elif sms_phone:
            title = f"📩 新短信来自 {sms_phone}"
        else:
            # 保留原始标题
            pass
    except Exception as e:
        logger.error(f"Bark 标题解析失败: {e}")

    # ===== Bark 推送 =====
    for key in keys:
        attempt = 0
        while attempt < BARK_MAX_RETRIES:
            try:
                safe_title = requests.utils.quote(title, safe='')
                safe_body = requests.utils.quote(body, safe='')
                url = f"{BARK_SERVER}/{key}/{safe_title}/{safe_body}"

                resp = requests.get(url, timeout=BARK_TIMEOUT)
                if resp.status_code in (200, 201):
                    logger.info(f"Bark 推送成功 (key={key})")
                    break
                else:
                    logger.error(f"Bark 推送失败 (key={key}) 状态码: {resp.status_code} 内容: {resp.text}")
            except Exception as e:
                logger.error(f"Bark 推送异常 (key={key}) 第 {attempt+1} 次: {e}")

            attempt += 1
            time.sleep(BARK_RETRY_DELAY)


def get_user_id(update: Update, context):
    """处理 /myid 命令，获取用户ID"""
    user_id = update.message.from_user.id
    update.message.reply_text(f"你的Telegram ID是: {user_id}")
    logger.info(f"用户 {user_id} 查询了其ID")

def send_sms(update: Update, context):
    """处理 /send 命令，执行asterisk发送短信"""
    user_id = update.message.from_user.id
    if user_id not in ALLOWED_IDS:
        update.message.reply_text("无权限使用此Bot！")
        logger.info(f"用户 {user_id} 尝试使用/send，无权限")
        return

    args = context.args
    if len(args) < 2:
        update.message.reply_text("用法: /send <phone_number> <message>")
        logger.warning(f"用户 {user_id} 输入无效/send命令: {args}")
        return

    phone_number = args[0]
    message = ' '.join(args[1:])  # 支持多词消息

    # 验证phone_number（简单检查，确保是数字）
    if not phone_number.isdigit():
        update.message.reply_text("电话号码必须是数字！")
        logger.warning(f"用户 {user_id} 输入无效电话号码: {phone_number}")
        return

    # 构建asterisk命令
    command = ['asterisk', '-rx', f'quectel sms quectel0 {phone_number} "{message}"']

    try:
        # 如果需要sudo，修改为：command = ['sudo', 'asterisk', '-rx', f'quectel sms quectel0 {phone_number} "{message}"']
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        update.message.reply_text(f"短信发送成功: {phone_number}\n输出: {result.stdout}")
        logger.info(f"用户 {user_id} 发送短信到 {phone_number}: {message}")
    except subprocess.CalledProcessError as e:
        update.message.reply_text(f"短信发送失败: {str(e)}\n错误: {e.stderr}")
        logger.error(f"用户 {user_id} 发送短信失败: {str(e)}")
    except Exception as e:
        update.message.reply_text(f"执行命令失败: {str(e)}")
        logger.error(f"用户 {user_id} 执行命令失败: {str(e)}")

def start_watching(bot, allowed_ids):
    """启动文件夹监控"""
    event_handler = MessageHandler(bot, allowed_ids)
    observer = Observer()
    observer.schedule(event_handler, MESSAGE_DIR, recursive=False)
    observer.start()
    logger.info("文件夹监控已启动")

def main():
    """主函数，启动Bot"""
    updater = Updater(TOKEN, use_context=True, request_kwargs=PROXY)
    dp = updater.dispatcher

    # 添加命令处理器
    dp.add_handler(CommandHandler("myid", get_user_id))
    dp.add_handler(CommandHandler("send", send_sms))

    # 错误处理
    def error(update, context):
        logger.error(f"更新 {update} 引发错误: {context.error}")

    dp.add_error_handler(error)

    # 启动文件夹监控
    start_watching(updater.bot, ALLOWED_IDS)

    # 启动Bot
    updater.start_polling()
    logger.info("Bot已启动")
    updater.idle()

if __name__ == '__main__':
    main() 