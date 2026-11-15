import json
import urllib.request
import subprocess
import re
import os

API_URL = "http://127.0.0.1:11434/api/chat"
MODEL = "qwen2.5-coder:1.5b"

def extract_all_json(text):
    # Убираем markdown-теги и лишние пробелы
    text = text.replace("```json", "").replace("```", "").strip()
    
    decoder = json.JSONDecoder()
    pos = 0
    results = []
    while pos < len(text):
        # Ищем начало JSON
        start = text.find('{', pos)
        if start == -1:
            break
        try:
            # Пытаемся распарсить объект
            obj, index = decoder.raw_decode(text[start:])
            results.append(obj)
            pos = start + index
        except json.JSONDecodeError:
            pos = start + 1
            
    return results

def agent_loop():
    print("=" * 50)
    print("🤖 Умный ИИ-Агент. Пиши 'выход' чтобы закончить.")
    print("=" * 50)
    
    messages = [
        {"role": "system", "content": (
            "Ты — дружелюбный ИИ-ассистент с навыками программиста.\n"
            "Если пользователь просто общается, отвечай обычным текстом.\n"
            "Если пользователь просит создать файл или выполнить команду, отвечай ТОЛЬКО чистым JSON без markdown-тегов ```json:\n"
            "{\"tool\": \"write_file\", \"args\": {\"path\": \"index.html\", \"content\": \"<html>...</html>\"}}\n"
            "{\"tool\": \"run_command\", \"args\": {\"cmd\": \"команда\"}}\n"
            "Если нужно сделать и то, и другое, напиши два JSON подряд."
        )}
    ]

    while True:
        try:
            user_input = input("\n👤 Ты: ")
            if user_input.lower() in ['выход', 'exit', 'quit']:
                print("Пока!")
                break
            
            messages.append({"role": "user", "content": user_input})
            
            data = {
                "model": MODEL,
                "messages": messages,
                "stream": True,
                "options": {"temperature": 0.1, "num_predict": 1000} 
            }
            
            req = urllib.request.Request(API_URL, data=json.dumps(data).encode(), headers={"Content-Type": "application/json"})
            
            print("🤖 ИИ: ", end="", flush=True)
            full_reply = ""
            is_action_mode = False

            with urllib.request.urlopen(req) as response:
                for line in response:
                    if line:
                        try:
                            chunk = json.loads(line.decode('utf-8'))
                            if "message" in chunk and "content" in chunk["message"]:
                                word = chunk["message"]["content"]
                                full_reply += word
                                
                                # Проверяем, начинается ли ответ с JSON или ```json
                                stripped = full_reply.strip()
                                if not is_action_mode and (stripped.startswith("{") or stripped.startswith("```json")):
                                    is_action_mode = True
                                    print("\r🤖 ИИ: [Генерирую код/команды...] ", end="", flush=True)
                                
                                # Если это обычный текст, печатаем его
                                if not is_action_mode:
                                    print(word, end="", flush=True)
                        except:
                            pass
            
            print("\n") # Перенос строки
            
            # Пытаемся найти все JSON команды в ответе
            actions = extract_all_json(full_reply)
            
            if actions:
                for action in actions:
                    tool = action.get("tool")
                    args = action.get("args", {})

                    if tool == "write_file":
                        path = args.get("path")
                        content = args.get("content", "")
                        try:
                            with open(path, "w", encoding="utf-8") as f:
                                f.write(content)
                            print(f"✅ СИСТЕМА: Файл `{path}` успешно создан!")
                        except Exception as e:
                            print(f"❌ Ошибка записи: {e}")

                    elif tool == "run_command":
                        cmd = args.get("cmd")
                        print(f"💻 СИСТЕМА: Выполняю: {cmd}")
                        try:
                            # Запуск сервера в фоне
                            if "http.server" in cmd or cmd.endswith("&"):
                                if not cmd.endswith("&"):
                                    cmd += " &"
                                subprocess.Popen(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                                print("🚀 СИСТЕМА: Сервер запущен в фоне!")
                            else:
                                # Обычные команды
                                res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=20)
                                if res.stdout:
                                    print(f"✅ Результат:\n{res.stdout.strip()}")
                                if res.stderr:
                                    print(f"⚠️ Ошибки:\n{res.stderr.strip()}")
                                if not res.stdout and not res.stderr:
                                    print("✅ СИСТЕМА: Команда выполнена.")
                        except Exception as e:
                            print(f"❌ Ошибка выполнения: {e}")
            else:
                if is_action_mode:
                    print("⚠️ СИСТЕМА: Модель попыталась написать код, но сломала JSON. Попроси еще раз.")

            messages.append({"role": "assistant", "content": full_reply})

        except KeyboardInterrupt:
            print("\nПока!")
            break
        except Exception as e:
            print(f"\n❌ Ошибка сети: {e}")

if __name__ == "__main__":
    agent_loop()
