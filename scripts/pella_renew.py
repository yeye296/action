#!/usr/bin/env python3
"""
Pella 自动续期脚本 增加重启功能（智能检测版）

配置变量说明:
- 单账号变量:
    - PELLA_EMAIL / LEAFLOW_EMAIL=登录邮箱
    - PELLA_PASSWORD / LEAFLOW_PASSWORD=登录密码
- 多账号变量:
    - PELLA_ACCOUNTS / LEAFLOW_ACCOUNTS: 格式：邮箱1:密码1,邮箱2:密码2,邮箱3:密码3
- 通知变量 (可选):
    - TG_BOT_TOKEN=Telegram 机器人 Token
    - TG_CHAT_ID=Telegram 聊天 ID
"""

import os
import time
import logging
import re
import requests
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def mask_email(email):
    """隐藏邮箱地址"""
    if not email or '@' not in email:
        return '***'
    name, domain = email.split('@', 1)
    if len(name) <= 2:
        masked = '*' * len(name)
    else:
        masked = name[0] + '*' * (len(name) - 2) + name[-1]
    return f"{masked}@{domain}"


def mask_url(url):
    """隐藏URL中的敏感ID"""
    if not url:
        return '***'
    match = re.search(r'/server/([a-f0-9]+)', url)
    if match:
        sid = match.group(1)
        if len(sid) > 8:
            return url.replace(sid, sid[:4] + '***' + sid[-4:])
    return url


class PellaAutoRenew:
    LOGIN_URL = "https://www.pella.app/login"
    HOME_URL = "https://www.pella.app/home"
    RENEW_WAIT_TIME = 8
    WAIT_TIME_AFTER_LOGIN = 20
    RESTART_WAIT_TIME = 60

    def __init__(self, email, password):
        self.email = email
        self.password = password
        self.initial_expiry_details = "N/A"
        self.initial_expiry_value = -1.0
        self.server_url = None
        self.restart_output = ""
        self.server_status = "unknown"
        
        if not self.email or not self.password:
            raise ValueError("邮箱和密码不能为空")
        
        self.driver = None
        self.setup_driver()
    
    def setup_driver(self):
        chrome_options = Options()
        
        if os.getenv('GITHUB_ACTIONS'):
            chrome_options.add_argument('--headless=new')
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--disable-dev-shm-usage')
            chrome_options.add_argument('--disable-gpu')
            chrome_options.add_argument('--window-size=1920,1080')
        
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        
        try:
            self.driver = webdriver.Chrome(options=chrome_options)
            self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        except WebDriverException as e:
            logger.error(f"❌ 驱动初始化失败: {e}")
            raise

    def wait_for_element_clickable(self, by, value, timeout=10):
        return WebDriverWait(self.driver, timeout).until(
            EC.element_to_be_clickable((by, value))
        )
    
    def wait_for_element_present(self, by, value, timeout=10):
        return WebDriverWait(self.driver, timeout).until(
            EC.presence_of_element_located((by, value))
        )

    def extract_expiry_days(self, page_source):
        match = re.search(r"Your server expires in\s*(\d+)D\s*(\d+)H\s*(\d+)M", page_source)
        if match:
            d, h, m = int(match.group(1)), int(match.group(2)), int(match.group(3))
            return f"{d}天{h}时{m}分", d + h/24 + m/1440
            
        match = re.search(r"Your server expires in\s*(\d+)D", page_source)
        if match:
            d = int(match.group(1))
            return f"{d}天", float(d)
            
        return "无法提取", -1.0

    def find_and_click_button(self):
        selectors = [
            "button.cl-formButtonPrimary",
            "button[data-localization-key='formButtonPrimary']",
            "//button[.//span[contains(text(), 'Continue')]]",
            "//button[contains(@class, 'cl-formButtonPrimary')]",
            "button[type='submit']",
            "form button"
        ]
        
        for selector in selectors:
            try:
                if selector.startswith("//"):
                    btn = WebDriverWait(self.driver, 3).until(
                        EC.element_to_be_clickable((By.XPATH, selector))
                    )
                else:
                    btn = WebDriverWait(self.driver, 3).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                    )
                
                self.driver.execute_script("arguments[0].scrollIntoView(true);", btn)
                time.sleep(0.3)
                self.driver.execute_script("arguments[0].click();", btn)
                return True
            except:
                continue
        return False

    def wait_for_password_field(self, timeout=15):
        selectors = [
            "input[type='password']",
            "input[name='password']",
            "input.cl-formFieldInput[type='password']",
            "#password",
        ]
        
        start = time.time()
        while time.time() - start < timeout:
            for sel in selectors:
                try:
                    elem = self.driver.find_element(By.CSS_SELECTOR, sel)
                    if elem.is_displayed():
                        return elem
                except:
                    pass
            time.sleep(0.5)
        return None

    def check_for_error(self):
        selectors = [
            ".cl-formFieldErrorText",
            "[data-localization-key*='error']",
            ".error-message",
        ]
        for sel in selectors:
            try:
                err = self.driver.find_element(By.CSS_SELECTOR, sel)
                if err.is_displayed():
                    return err.text
            except:
                pass
        return None

    def login(self):
        logger.info("开始登录")
        self.driver.get(self.LOGIN_URL)
        time.sleep(4)
        
        def js_set_value(element, value):
            element.clear()
            element.click()
            time.sleep(0.2)
            element.send_keys(value)
            time.sleep(0.2)
            self.driver.execute_script("""
                arguments[0].value = arguments[1];
                arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
                arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
            """, element, value)
        
        try:
            email_input = self.wait_for_element_present(By.CSS_SELECTOR, "input[name='identifier']", 15)
            js_set_value(email_input, self.email)
            if email_input.get_attribute('value') != self.email:
                email_input.clear()
                email_input.send_keys(self.email)
            logger.info("✅ 邮箱输入完成")
        except Exception as e:
            raise Exception(f"❌ 输入邮箱失败: {e}")
            
        try:
            time.sleep(1)
            if not self.find_and_click_button():
                raise Exception("❌ 无法点击Continue按钮")
            
            password_input = self.wait_for_password_field(timeout=15)
            if not password_input:
                error = self.check_for_error()
                if error:
                    raise Exception(f"❌ 登录错误: {error}")
                raise Exception("❌ 密码框未出现")
            
            logger.info("✅ 进入密码步骤")
            time.sleep(1)
        except Exception as e:
            raise Exception(f"❌ 第一步失败: {e}")

        try:
            password_input = self.wait_for_element_present(By.CSS_SELECTOR, "input[type='password']", 10)
            js_set_value(password_input, self.password)
            logger.info("✅ 密码输入完成")
        except Exception as e:
            raise Exception(f"❌ 输入密码失败: {e}")

        try:
            time.sleep(2)
            if not self.find_and_click_button():
                raise Exception("❌ 无法点击登录按钮")
        except Exception as e:
            raise Exception(f"❌ 点击登录失败: {e}")

        try:
            for _ in range(self.WAIT_TIME_AFTER_LOGIN // 2):
                time.sleep(2)
                url = self.driver.current_url
                
                if '/home' in url or '/dashboard' in url:
                    logger.info("✅ 登录成功")
                    return True
                
                error = self.check_for_error()
                if error:
                    raise Exception(f"❌ 登录失败: {error}")
                
                if '/login' not in url and '/sign-in' not in url:
                    self.driver.get(self.HOME_URL)
                    time.sleep(2)
                    if '/home' in self.driver.current_url:
                        logger.info("✅ 登录成功")
                        return True
            
            self.driver.get(self.HOME_URL)
            time.sleep(3)
            if '/home' in self.driver.current_url:
                logger.info("✅ 登录成功")
                return True
            
            raise Exception("❌ 登录超时")
        except Exception as e:
            raise Exception(f"❌ 登录验证失败: {e}")

    def get_server_url(self):
        if '/home' not in self.driver.current_url:
            self.driver.get(self.HOME_URL)
            time.sleep(3)
            
        try:
            link = self.wait_for_element_clickable(By.CSS_SELECTOR, "a[href*='/server/']", 15)
            link.click()
            WebDriverWait(self.driver, 10).until(EC.url_contains("/server/"))
            self.server_url = self.driver.current_url
            logger.info(f"✅ 服务器: {mask_url(self.server_url)}")
            return True
        except Exception as e:
            raise Exception(f"❌ 获取服务器失败: {e}")
    
    def check_server_status(self):
        """检查服务器当前状态"""
        if not self.server_url:
            return "unknown"
        
        if '/server/' not in self.driver.current_url:
            self.driver.get(self.server_url)
            time.sleep(3)
        
        page_text = self.driver.find_element(By.TAG_NAME, "body").text.upper()
        
        # 检查运行状态指示
        running_indicators = ["STATUS: RUNNING", "RUNNING", "ONLINE", "ACTIVE"]
        stopped_indicators = ["STATUS: STOPPED", "STOPPED", "OFFLINE", "INACTIVE", "NOT RUNNING"]
        
        # 检查页面状态元素
        try:
            status_elements = self.driver.find_elements(By.XPATH, 
                "//*[contains(text(), 'Status') or contains(text(), 'status')]")
            
            for elem in status_elements:
                try:
                    parent = elem.find_element(By.XPATH, "./..")
                    parent_text = parent.text.upper()
                    
                    for indicator in running_indicators:
                        if indicator in parent_text:
                            logger.info("✅ 服务器状态: 运行中")
                            self.server_status = "running"
                            return "running"
                    
                    for indicator in stopped_indicators:
                        if indicator in parent_text:
                            logger.info("⚠️ 服务器状态: 已停止")
                            self.server_status = "stopped"
                            return "stopped"
                except:
                    continue
        except:
            pass
        
        # 检查是否有 START 按钮（有则说明服务器已停止）
        try:
            start_buttons = self.driver.find_elements(By.XPATH, 
                "//button[contains(text(), 'START') and not(contains(text(), 'RESTART'))]")
            
            for btn in start_buttons:
                if btn.is_displayed() and btn.is_enabled():
                    btn_text = btn.text.upper().strip()
                    if btn_text == "START" or btn_text == "START SERVER":
                        logger.info("⚠️ 服务器状态: 已停止 (发现START按钮)")
                        self.server_status = "stopped"
                        return "stopped"
        except:
            pass
        
        # 通过页面文本检查
        for indicator in running_indicators:
            if indicator in page_text:
                logger.info("✅ 服务器状态: 运行中")
                self.server_status = "running"
                return "running"
        
        for indicator in stopped_indicators:
            if indicator in page_text:
                logger.info("⚠️ 服务器状态: 已停止")
                self.server_status = "stopped"
                return "stopped"
        
        logger.info("❓ 服务器状态: 无法确定")
        self.server_status = "unknown"
        return "unknown"
    
    def isServiceAlive(self, test_url: str = "http://pella.kisskiss.cloudns.ch", timeout: int = 5) -> bool:
        try:
            resp = requests.get(test_url, timeout=timeout)
            if resp.status_code in (200, 404):
                logger.info(f"✅ 隧道可用: {resp.status_code}")
                return True
            else:
                logger.info(f"❌ 隧道不可用: {resp.status_code}")
                return False
        except Exception as e:
            logger.info(f"❌ 隧道链接访问失败: {e}")
            return False
    
    def renew_server(self):
        if not self.server_url:
            raise Exception("❌ 缺少服务器URL")
            
        self.driver.get(self.server_url)
        time.sleep(15)

        self.initial_expiry_details, self.initial_expiry_value = self.extract_expiry_days(self.driver.page_source)
        logger.info(f"📅 当前过期: {self.initial_expiry_details}")

        if self.initial_expiry_value == -1.0:
            raise Exception("❌ 无法提取过期时间")

        try:
            selector = "a[href*='/renew/']:not(.opacity-50):not(.pointer-events-none)"
            count = 0
            original = self.driver.current_window_handle
            
            while True:
                buttons = self.driver.find_elements(By.CSS_SELECTOR, selector)
                if not buttons:
                    break

                url = buttons[0].get_attribute('href')
                logger.info(f"续期 #{count + 1}")
                
                self.driver.execute_script("window.open(arguments[0]);", url)
                time.sleep(1)
                self.driver.switch_to.window(self.driver.window_handles[-1])
                time.sleep(self.RENEW_WAIT_TIME)
                self.driver.close()
                self.driver.switch_to.window(original)
                count += 1
                
                self.driver.get(self.server_url)
                time.sleep(3)

            if count == 0:
                disabled = self.driver.find_elements(By.CSS_SELECTOR, "a[href*='/renew/'].opacity-50")
                return "📅 今日已续期" if disabled else "❌ 未找到续期按钮"

            self.driver.get(self.server_url)
            time.sleep(5)
            
            final, final_val = self.extract_expiry_days(self.driver.page_source)
            logger.info(f"📅 续期后: {final}")
            
            if final_val > self.initial_expiry_value:
                return f"✅ 续期成功 {self.initial_expiry_details} -> {final}"
            return f"❌ 天数未变化 ({final})"

        except Exception as e:
            raise Exception(f"❌ 续期错误: {e}")

    def restart_server(self):
        """点击重启按钮（仅在服务器停止时执行）"""
        if not self.server_url:
            logger.warning("⚠️ 缺少服务器URL，跳过重启")
            return False, "跳过: 缺少服务器URL"
        
        # 先检查服务器状态
        status = self.check_server_status()
        isAlive = self.isServiceAlive()
        if isAlive: 
            logger.info("✅ 服务器正在运行，无需重启")
            return True
        
        # if status == "running":
        #     logger.info("✅ 服务器正在运行，无需重启")
        #     return True, "跳过: 服务器正在运行"
        
        # if status == "unknown":
        #     logger.info("❓ 无法确定服务器状态，跳过重启")
        #     return False, "跳过: 无法确定服务器状态"
        
        logger.info("🔄 服务器已停止，开始重启...")
        
        if '/server/' not in self.driver.current_url:
            self.driver.get(self.server_url)
            time.sleep(3)
        
        try:
            restart_btn = None
            selectors = [
                "//button[contains(text(), 'RESTART')]",
                "//button[.//text()[contains(., 'RESTART')]]",
            ]
            
            for sel in selectors:
                try:
                    restart_btn = WebDriverWait(self.driver, 5).until(
                        EC.element_to_be_clickable((By.XPATH, sel))
                    )
                    if restart_btn:
                        break
                except:
                    continue
            
            if not restart_btn:
                buttons = self.driver.find_elements(By.TAG_NAME, "button")
                for btn in buttons:
                    try:
                        if 'RESTART' in btn.text.upper():
                            restart_btn = btn
                            break
                    except:
                        continue
            
            if not restart_btn:
                logger.warning("⚠️ 未找到 RESTART 按钮")
                return False, "未找到 RESTART 按钮"
            
            self.driver.execute_script("arguments[0].scrollIntoView(true);", restart_btn)
            time.sleep(0.5)
            self.driver.execute_script("arguments[0].click();", restart_btn)
            logger.info("✅ 已点击 RESTART 按钮")
            
            output = self._wait_for_restart_output()
            self.restart_output = output
            
            if output:
                logger.info(f"✅ 重启完成，获取到 {len(output)} 字符的输出")
                return True, output
            else:
                logger.warning("⚠️ 未获取到重启输出")
                return False, "未获取到重启输出"
                
        except Exception as e:
            logger.error(f"❌ 重启失败: {e}")
            return False, f"重启失败: {e}"

    def _wait_for_restart_output(self):
        """等待重启输出完成并返回输出内容"""
        logger.info("⏳ 等待重启输出...")
        
        start_time = time.time()
        last_output = ""
        stable_count = 0
        
        while time.time() - start_time < self.RESTART_WAIT_TIME:
            try:
                pre_elements = self.driver.find_elements(By.CSS_SELECTOR, "pre.bg-black, pre[class*='bg-black']")
                
                if not pre_elements:
                    pre_elements = self.driver.find_elements(By.TAG_NAME, "pre")
                
                current_output = ""
                for pre in pre_elements:
                    try:
                        divs = pre.find_elements(By.TAG_NAME, "div")
                        for div in divs:
                            text = div.text.strip()
                            if text and text != "Copy":
                                current_output += text + "\n"
                        
                        if not current_output:
                            current_output = pre.text
                    except:
                        continue
                
                if current_output:
                    completion_markers = [
                        "App is running",
                        "Thank you for using this script",
                        "enjoy!"
                    ]
                    
                    is_complete = any(marker in current_output for marker in completion_markers)
                    
                    if current_output == last_output:
                        stable_count += 1
                    else:
                        stable_count = 0
                        last_output = current_output
                    
                    if is_complete and stable_count >= 2:
                        return self._clean_output(current_output)
                
                time.sleep(2)
                
            except Exception as e:
                logger.debug(f"获取输出时出错: {e}")
                time.sleep(2)
        
        if last_output:
            return self._clean_output(last_output)
        return ""

    def _clean_output(self, output):
        """清理输出内容"""
        if not output:
            return ""
        
        lines = output.split('\n')
        cleaned_lines = []
        
        for line in lines:
            line = line.strip()
            if not line or line == "Copy":
                continue
            line = re.sub(r'\[\d+;\d+H|\[\d+J|\[0J', '', line)
            cleaned_lines.append(line)
        
        return '\n'.join(cleaned_lines)
            
    def run(self):
        try:
            logger.info(f"处理账号: {mask_email(self.email)}")
            
            if self.login() and self.get_server_url():
                result = self.renew_server()
                logger.info(f"续期结果: {result}")
                
                restart_success, restart_output = self.restart_server()
                
                return True, result, restart_output
                
            return False, "❌ 登录或获取服务器失败", ""
                
        except Exception as e:
            logger.error(f"❌ 失败: {e}")
            return False, f"❌ 失败: {e}", ""
        finally:
            if self.driver:
                self.driver.quit()


class MultiAccountManager:
    def __init__(self):
        self.tg_token = os.getenv('TG_BOT_TOKEN', '')
        self.tg_chat = os.getenv('TG_CHAT_ID', '')
        self.accounts = self.load_accounts()
    
    def load_accounts(self):
        accounts = []
        
        accounts_str = os.getenv('PELLA_ACCOUNTS', os.getenv('LEAFLOW_ACCOUNTS', '')).strip()
        if accounts_str:
            for pair in [p.strip() for p in re.split(r'[;,]', accounts_str) if p.strip()]:
                if ':' in pair:
                    email, pwd = pair.split(':', 1)
                    if email.strip() and pwd.strip():
                        accounts.append({'email': email.strip(), 'password': pwd.strip()})
            if accounts:
                logger.info(f"加载 {len(accounts)} 个账号")
                return accounts
        
        email = os.getenv('PELLA_EMAIL', os.getenv('LEAFLOW_EMAIL', '')).strip()
        pwd = os.getenv('PELLA_PASSWORD', os.getenv('LEAFLOW_PASSWORD', '')).strip()
        
        if email and pwd:
            accounts.append({'email': email, 'password': pwd})
            logger.info("加载单账号")
            return accounts
        
        raise ValueError("❌ 未找到账号配置")
    
    def send_notification(self, results):
        """发送通知 - 每个账号单独一条消息"""
        if not self.tg_token or not self.tg_chat:
            return
        
        for email, success, result, restart_output in results:
            try:
                self._send_single_notification(email, success, result, restart_output)
                time.sleep(0.5)
            except Exception as e:
                logger.error(f"❌ 发送 {mask_email(email)} 通知失败: {e}")
    
    def _send_single_notification(self, email, success, result, restart_output):
        """发送单个账号的通知"""
        try:
            # 确定状态图标
            if "成功" in result:
                status = "✅"
            elif "已续期" in result:
                status = "📅"
            else:
                status = "❌"
            
            # 确定重启状态
            if "跳过: 服务器正在运行" in restart_output:
                restart_status = "✅ 运行中(无需重启)"
            elif "跳过" in restart_output:
                restart_status = f"⏭️ {restart_output}"
            elif restart_output and ("App is running" in restart_output or "running" in restart_output.lower()):
                restart_status = "✅ 重启完成"
            elif restart_output:
                restart_status = "⚠️ 未确认"
            else:
                restart_status = "⚠️ 无输出"
            
            # 构建简洁消息
            msg = f"🎁 Pella 续期报告\n"
            msg += f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
            msg += "━" * 18 + "\n\n"
            msg += f"{status} {email}\n"
            msg += f"├ 续期: {result}\n"
            msg += f"└ 重启: {restart_status}"
            
            # 发送主消息
            response = requests.post(
                f"https://api.telegram.org/bot{self.tg_token}/sendMessage",
                data={"chat_id": self.tg_chat, "text": msg},
                timeout=10
            )
            
            if response.status_code == 200:
                logger.info(f"✅ {mask_email(email)} 消息已发送")
                message_id = response.json().get('result', {}).get('message_id')
                
                # 如果有实际的重启日志（不是跳过消息），作为文件发送
                if restart_output and len(restart_output) > 50 and "跳过" not in restart_output:
                    self._send_log_file(email, restart_output, message_id)
            else:
                logger.warning(f"⚠️ 发送失败: {response.text}")
                
        except Exception as e:
            logger.error(f"❌ 通知失败: {e}")
    
    def _send_log_file(self, email, log_content, reply_to_message_id=None):
        """将日志作为文件发送"""
        try:
            import io
            
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"restart_log_{timestamp}.txt"
            
            file_content = f"Pella 重启日志\n"
            file_content += f"账号: {email}\n"
            file_content += f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            file_content += "=" * 50 + "\n\n"
            file_content += log_content
            
            file_obj = io.BytesIO(file_content.encode('utf-8'))
            file_obj.name = filename
            
            data = {
                "chat_id": self.tg_chat,
                "caption": "📜 重启日志",
                "disable_notification": True
            }
            
            if reply_to_message_id:
                data["reply_to_message_id"] = reply_to_message_id
            
            response = requests.post(
                f"https://api.telegram.org/bot{self.tg_token}/sendDocument",
                data=data,
                files={"document": (filename, file_obj, "text/plain")},
                timeout=30
            )
            
            if response.status_code == 200:
                logger.info(f"✅ {mask_email(email)} 日志文件已发送")
            else:
                logger.warning(f"⚠️ 日志文件发送失败: {response.text}")
                
        except Exception as e:
            logger.error(f"❌ 发送日志文件失败: {e}")
    
    def run_all(self):
        results = []
        total = len(self.accounts)
        
        for i, acc in enumerate(self.accounts, 1):
            logger.info(f"[{i}/{total}] {mask_email(acc['email'])}")
            
            try:
                renew = PellaAutoRenew(acc['email'], acc['password'])
                success, result, restart_output = renew.run()
                if i < total:
                    time.sleep(5)
            except Exception as e:
                success, result, restart_output = False, f"❌ 异常: {e}", ""
            
            results.append((acc['email'], success, result, restart_output))
        
        self.send_notification(results)
        return all(s for _, s, _, _ in results), results


def main():
    try:
        manager = MultiAccountManager()
        manager.run_all()
    except Exception as e:
        logger.error(f"❌ 错误: {e}")
        exit(1)


if __name__ == "__main__":
    main()
