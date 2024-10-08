import os
import sys
import traceback
import time
import os, threading
from concurrent.futures import ThreadPoolExecutor,wait,ALL_COMPLETED,FIRST_COMPLETED, as_completed


import subprocess
from multiprocessing import Process, Value, freeze_support
from multiprocessing.connection import Listener
from multiprocessing.connection import Client
import concurrent

import json

import copy
import torch
import shutil
import logging
import yaml
import argparse

from PIL import Image, ImageDraw
import dashscope


from MobileAgent.api import inference_chat
from MobileAgent.text_localization import ocr
from MobileAgent.icon_localization import det
from MobileAgent.controller import get_screenshot, tap, slide, type, back, home
from MobileAgent.prompt import get_action_prompt, get_reflect_prompt, get_memory_prompt, get_process_prompt
from MobileAgent.chat import init_action_chat, init_reflect_chat, init_memory_chat, add_response, add_response_two_image


####################################### Edit your Setting #########################################
with open('./config.yaml', 'r', encoding='UTF-8') as file:
    prime_service = yaml.safe_load(file)
    
# Your ADB path
adb_path = prime_service['adb_path']

# Your instruction
instruction = ""

# Your GPT-4o API URL
API_url = prime_service['API_url']

# Your GPT-4o API Token
token = prime_service['token']

# model version
llm_model = prime_service['model']
planning_model = prime_service['planning_model']

# Choose between "api" and "local". api: use the qwen api. local: use the local qwen checkpoint
caption_call_method = "api"

# Choose between "qwen-vl-plus" and "qwen-vl-max" if use api method. Choose between "qwen-vl-chat" and "qwen-vl-chat-int4" if use local method.
caption_model = "qwen-vl-plus"

# If you choose the api caption call method, input your Qwen api here
qwen_api = prime_service['qwen_api']

# You can add operational knowledge to help Agent operate more accurately.
add_info = prime_service['add_info']

# Reflection Setting: If you want to improve the operating speed, you can disable the reflection agent. This may reduce the success rate.
reflection_switch = prime_service['reflection_switch']

# Memory Setting: If you want to improve the operating speed, you can disable the memory unit. This may reduce the success rate.
memory_switch = prime_service['memory_switch']

connection_port =  prime_service['connection_port'] if 'connection_port' in prime_service else 25000

mock_result = prime_service['mock_result']

# actions
# actions = prime_service['actions']
# print(actions)
# print(type(actions))
with open('./actions.json', 'r', encoding='UTF-8') as actionsfile:
    actions = json.load(actionsfile)

###################################################################################################

# ocr_detection=''
# ocr_recognition= ''
# groundingdino_model = ''
# temp_file = ''
# tokenizer = ''
model = ''


def get_all_files_in_folder(folder_path):
    file_list = []
    for file_name in os.listdir(folder_path):
        file_list.append(file_name)
    return file_list


def draw_coordinates_on_image(image_path, coordinates):
    image = Image.open(image_path)
    draw = ImageDraw.Draw(image)
    point_size = 10
    for coord in coordinates:
        draw.ellipse((coord[0] - point_size, coord[1] - point_size, coord[0] + point_size, coord[1] + point_size), fill='red')
    output_image_path = './screenshot/output_image.png'
    image.save(output_image_path)
    return output_image_path


def crop(image, box, i):
    image = Image.open(image)
    x1, y1, x2, y2 = int(box[0]), int(box[1]), int(box[2]), int(box[3])
    if x1 >= x2-10 or y1 >= y2-10:
        return
    cropped_image = image.crop((x1, y1, x2, y2))
    cropped_image.save(f"./temp/{i}.jpg")


def generate_local(tokenizer, model, image_file, query):
    query = tokenizer.from_list_format([
        {'image': image_file},
        {'text': query},
    ])
    response, _ = model.chat(tokenizer, query=query, history=None)
    return response


def process_image(image, query):
    dashscope.api_key = qwen_api
    image = "file://" + image
    messages = [{
        'role': 'user',
        'content': [
            {
                'image': image
            },
            {
                'text': query
            },
        ]
    }]
    response = dashscope.MultiModalConversation.call(model=caption_model, messages=messages)
    
    try:
        response = response['output']['choices'][0]['message']['content'][0]["text"]
    except:
        response = "This is an icon."
    
    return response


def generate_api(images, query):
    icon_map = {}
    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = {executor.submit(process_image, image, query): i for i, image in enumerate(images)}
        
        for future in concurrent.futures.as_completed(futures):
            i = futures[future]
            response = future.result()
            icon_map[i + 1] = response
    
    return icon_map


def merge_text_blocks(text_list, coordinates_list):
    merged_text_blocks = []
    merged_coordinates = []

    sorted_indices = sorted(range(len(coordinates_list)), key=lambda k: (coordinates_list[k][1], coordinates_list[k][0]))
    sorted_text_list = [text_list[i] for i in sorted_indices]
    sorted_coordinates_list = [coordinates_list[i] for i in sorted_indices]

    num_blocks = len(sorted_text_list)
    merge = [False] * num_blocks

    for i in range(num_blocks):
        if merge[i]:
            continue
        
        anchor = i
        
        group_text = [sorted_text_list[anchor]]
        group_coordinates = [sorted_coordinates_list[anchor]]

        for j in range(i+1, num_blocks):
            if merge[j]:
                continue

            if abs(sorted_coordinates_list[anchor][0] - sorted_coordinates_list[j][0]) < 10 and \
            sorted_coordinates_list[j][1] - sorted_coordinates_list[anchor][3] >= -10 and sorted_coordinates_list[j][1] - sorted_coordinates_list[anchor][3] < 30 and \
            abs(sorted_coordinates_list[anchor][3] - sorted_coordinates_list[anchor][1] - (sorted_coordinates_list[j][3] - sorted_coordinates_list[j][1])) < 10:
                group_text.append(sorted_text_list[j])
                group_coordinates.append(sorted_coordinates_list[j])
                merge[anchor] = True
                anchor = j
                merge[anchor] = True

        merged_text = "\n".join(group_text)
        min_x1 = min(group_coordinates, key=lambda x: x[0])[0]
        min_y1 = min(group_coordinates, key=lambda x: x[1])[1]
        max_x2 = max(group_coordinates, key=lambda x: x[2])[2]
        max_y2 = max(group_coordinates, key=lambda x: x[3])[3]

        merged_text_blocks.append(merged_text)
        merged_coordinates.append([min_x1, min_y1, max_x2, max_y2])

    return merged_text_blocks, merged_coordinates


def get_perception_infos(adb_path, screenshot_file, groundingdino_model):
    get_screenshot(adb_path)
    
    width, height = Image.open(screenshot_file).size
    
    text, coordinates = ocr(screenshot_file, ocr_detection, ocr_recognition)
    text, coordinates = merge_text_blocks(text, coordinates)
    
    center_list = [[(coordinate[0]+coordinate[2])/2, (coordinate[1]+coordinate[3])/2] for coordinate in coordinates]
    draw_coordinates_on_image(screenshot_file, center_list)
    
    perception_infos = []
    for i in range(len(coordinates)):
        perception_info = {"text": "text: " + text[i], "coordinates": coordinates[i]}
        perception_infos.append(perception_info)
        
    coordinates = det(screenshot_file, "icon", groundingdino_model)
    
    for i in range(len(coordinates)):
        perception_info = {"text": "icon", "coordinates": coordinates[i]}
        perception_infos.append(perception_info)
        
    image_box = []
    image_id = []
    for i in range(len(perception_infos)):
        if perception_infos[i]['text'] == 'icon':
            image_box.append(perception_infos[i]['coordinates'])
            image_id.append(i)

    for i in range(len(image_box)):
        crop(screenshot_file, image_box[i], image_id[i])

    images = get_all_files_in_folder(temp_file)
    if len(images) > 0:
        images = sorted(images, key=lambda x: int(x.split('/')[-1].split('.')[0]))
        image_id = [int(image.split('/')[-1].split('.')[0]) for image in images]
        icon_map = {}
        prompt = 'This image is an icon from a phone screen. Please briefly describe the shape and color of this icon in one sentence.'
        if caption_call_method == "local":
            for i in range(len(images)):
                image_path = os.path.join(temp_file, images[i])
                icon_width, icon_height = Image.open(image_path).size
                if icon_height > 0.8 * height or icon_width * icon_height > 0.2 * width * height:
                    des = "None"
                else:
                    des = generate_local(tokenizer, model, image_path, prompt)
                icon_map[i+1] = des
        else:
            for i in range(len(images)):
                images[i] = os.path.join(temp_file, images[i])
            icon_map = generate_api(images, prompt)
        for i, j in zip(image_id, range(1, len(image_id)+1)):
            if icon_map.get(j):
                perception_infos[i]['text'] = "icon: " + icon_map[j]

    for i in range(len(perception_infos)):
        perception_infos[i]['coordinates'] = [int((perception_infos[i]['coordinates'][0]+perception_infos[i]['coordinates'][2])/2), int((perception_infos[i]['coordinates'][1]+perception_infos[i]['coordinates'][3])/2)]
        
    return perception_infos, width, height

def start_server():
    from modelscope.pipelines import pipeline
    from modelscope.utils.constant import Tasks
    from modelscope import snapshot_download, AutoModelForCausalLM, AutoTokenizer, GenerationConfig
        ### Load caption model ###
    device = "cuda"
    torch.manual_seed(1234)
    if caption_call_method == "local":
        if caption_model == "qwen-vl-chat":
            model_dir = snapshot_download('qwen/Qwen-VL-Chat', revision='v1.1.0')
            model = AutoModelForCausalLM.from_pretrained(model_dir, device_map=device, trust_remote_code=True).eval()
            model.generation_config = GenerationConfig.from_pretrained(model_dir, trust_remote_code=True)
        elif caption_model == "qwen-vl-chat-int4":
            qwen_dir = snapshot_download("qwen/Qwen-VL-Chat-Int4", revision='v1.0.0')
            model = AutoModelForCausalLM.from_pretrained(qwen_dir, device_map=device, trust_remote_code=True,use_safetensors=True).eval()
            model.generation_config = GenerationConfig.from_pretrained(qwen_dir, trust_remote_code=True, do_sample=False)
        else:
            print("If you choose local caption method, you must choose the caption model from \"Qwen-vl-chat\" and \"Qwen-vl-chat-int4\"")
            exit(0)
        global tokenizer
        tokenizer = AutoTokenizer.from_pretrained(qwen_dir, trust_remote_code=True)
    elif caption_call_method == "api":
        pass
    else:
        print("You must choose the caption model call function from \"local\" and \"api\"")
        exit(0)


    ### Load ocr and icon detection model ###
    groundingdino_dir = snapshot_download('AI-ModelScope/GroundingDINO', revision='v1.0.0')
    global groundingdino_model
    groundingdino_model = pipeline('grounding-dino-task', model=groundingdino_dir)
    global ocr_detection
    ocr_detection = pipeline(Tasks.ocr_detection, model='damo/cv_resnet18_ocr-detection-line-level_damo')

    global ocr_recognition
    ocr_recognition = pipeline(Tasks.ocr_recognition, model='damo/cv_convnextTiny_ocr-recognition-document_damo')

    flag = Value('i', False)
    # p = Process(target=echo_server, args=(('', connection_port), b'stop_an_action', flag))
    # p.start()
    serv = Listener(('', connection_port), authkey=b'stop_an_action')
    pool= ThreadPoolExecutor(max_workers=100)

    while True:
        try:
            client = serv.accept()
            print("接收到客户端指令")
            pool.submit(echo_client, client, flag, groundingdino_model, ocr_detection) 
            print("当前线程数量：" + str(len(pool._threads)))
            # subprocess.Popen(echo_client(client, flag, groundingdino_model, ocr_detection))
            # p = Process(target=echo_client, args=(client, flag, groundingdino_model, ocr_detection))
            # p.start()
        except Exception:
            traceback.print_exc()

def do_run(instruction, flag, groundingdino_model, ocr_detection):
    ### init logging ###
    # Create a logger and set the log level to INFO
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)

    # Add a StreamHandler to send log messages to console
    console_handler = logging.StreamHandler(sys.stdout)
    logger.addHandler(console_handler)    

    if(instruction is None):
        logger.error("未提供指令，请使用 --action 或 --prompt ")
        return
    
    if(flag.value):
        logger.warning('user has aborted this action')
        logger.info('final result: 取消')
        return
    
    thread_id = threading.current_thread().ident
    logger.info(f'用户提供的指令是: {instruction}, thread_id={thread_id}')

    millis = int(round(time.time() * 1000))
    file_handler = logging.FileHandler(f'logs/run_log{instruction}_{millis}.log')
    logger.addHandler(file_handler)

    thought_history = []
    summary_history = []
    action_history = []
    summary = ""
    action = ""
    completed_requirements = ""
    memory = ""
    insight = ""
    global temp_file
    temp_file = "temp"
    global screenshot
    screenshot = "screenshot"
    if not os.path.exists(temp_file):
        os.mkdir(temp_file)
    else:
        shutil.rmtree(temp_file)
        os.mkdir(temp_file)
    if not os.path.exists(screenshot):
        os.mkdir(screenshot)
    error_flag = False
    print('====================')
    iter = 0
    while True:
        if(flag.value):
            logger.warning('user has aborted this action')
            logger.info('final result: 取消')
            return
        
        print('--------------------------------')
        iter += 1
        if iter == 1 and not flag.value:
            screenshot_file = "./screenshot/screenshot.jpg"
            perception_infos, width, height = get_perception_infos(adb_path, screenshot_file, groundingdino_model)
            shutil.rmtree(temp_file)
            os.mkdir(temp_file)
            
            keyboard = False
            keyboard_height_limit = 0.9 * height
            for perception_info in perception_infos:
                if perception_info['coordinates'][1] < keyboard_height_limit:
                    continue
                if 'ADB Keyboard' in perception_info['text']:
                    keyboard = True
                    break

        prompt_action = get_action_prompt(instruction, perception_infos, width, height, keyboard, summary_history, action_history, summary, action, add_info, error_flag, completed_requirements, memory)
        chat_action = init_action_chat()
        chat_action = add_response("user", prompt_action, chat_action, screenshot_file)

        output_action = inference_chat(chat_action, llm_model, API_url, token, logger, flag.value)
        if(output_action is None):
            return
        
        thought = output_action.split("### Thought ###")[-1].split("### Action ###")[0].replace("\n", " ").replace(":", "").replace("  ", " ").strip()
        summary = output_action.split("### Operation ###")[-1].replace("\n", " ").replace("  ", " ").strip()
        action = output_action.split("### Action ###")[-1].split("### Operation ###")[0].replace("\n", " ").replace("  ", " ").strip()
        chat_action = add_response("assistant", output_action, chat_action)
        status = "#" * 50 + " Decision " + "#" * 50
        logger.info(status)
        logger.info(output_action)
        logger.info('#' * len(status))
        
        if memory_switch:
            prompt_memory = get_memory_prompt(insight)
            chat_action = add_response("user", prompt_memory, chat_action)
            output_memory = inference_chat(chat_action, llm_model, API_url, token, logger, flag.value)
            if(output_memory is None):
                return
        
            chat_action = add_response("assistant", output_memory, chat_action)
            status = "#" * 50 + " Memory " + "#" * 50
            logger.info(status)
            logger.info(output_memory)
            logger.info('#' * len(status))
            output_memory = output_memory.split("### Important content ###")[-1].split("\n\n")[0].strip() + "\n"
            if "None" not in output_memory and output_memory not in memory:
                memory += output_memory
        
        if "Open app" in action:
            app_name = action.split("(")[-1].split(")")[0]
            text, coordinate = ocr(screenshot_file, ocr_detection, ocr_recognition)
            tap_coordinate = [0, 0]
            for ti in range(len(text)):
                if app_name == text[ti]:
                    name_coordinate = [int((coordinate[ti][0] + coordinate[ti][2])/2), int((coordinate[ti][1] + coordinate[ti][3])/2)]
                    tap(adb_path, name_coordinate[0], name_coordinate[1]- int(coordinate[ti][3] - coordinate[ti][1]))# 
        
        elif "Tap" in action:
            coordinate = action.split("(")[-1].split(")")[0].split(", ")
            x, y = int(coordinate[0]), int(coordinate[1])
            tap(adb_path, x, y)
        
        elif "Swipe" in action:
            coordinate1 = action.split("Swipe (")[-1].split("), (")[0].split(", ")
            coordinate2 = action.split("), (")[-1].split(")")[0].split(", ")
            x1, y1 = int(coordinate1[0]), int(coordinate1[1])
            x2, y2 = int(coordinate2[0]), int(coordinate2[1])
            slide(adb_path, x1, y1, x2, y2)
            
        elif "Type" in action:
            if "(text)" not in action:
                text = action.split("(")[-1].split(")")[0]
            else:
                text = action.split(" \"")[-1].split("\"")[0]
            type(adb_path, text)
        
        elif "Back" in action:
            back(adb_path)
        
        elif "Home" in action:
            home(adb_path)
            
        elif "Stop" in action:
            exe_result = "成功"
            if mock_result == 1:
                exe_result = "成功"
            elif mock_result == 2:
                exe_result = "失败"
            logger.info("final result: " + exe_result)
            break
        
        time.sleep(5)
        
        last_perception_infos = copy.deepcopy(perception_infos)
        last_screenshot_file = "./screenshot/last_screenshot.jpg"
        last_keyboard = keyboard
        if os.path.exists(last_screenshot_file):
            os.remove(last_screenshot_file)
        os.rename(screenshot_file, last_screenshot_file)
        
        perception_infos, width, height = get_perception_infos(adb_path, screenshot_file, groundingdino_model)
        shutil.rmtree(temp_file)
        os.mkdir(temp_file)
        
        keyboard = False
        for perception_info in perception_infos:
            if perception_info['coordinates'][1] < keyboard_height_limit:
                continue
            if 'ADB Keyboard' in perception_info['text']:
                keyboard = True
                break
        
        if reflection_switch:
            prompt_reflect = get_reflect_prompt(instruction, last_perception_infos, perception_infos, width, height, last_keyboard, keyboard, summary, action, add_info)
            chat_reflect = init_reflect_chat()
            chat_reflect = add_response_two_image("user", prompt_reflect, chat_reflect, [last_screenshot_file, screenshot_file])

            output_reflect = inference_chat(chat_reflect, llm_model, API_url, token, logger, flag.value)
            if(output_reflect is None):
                return
            reflect = output_reflect.split("### Answer ###")[-1].replace("\n", " ").strip()
            chat_reflect = add_response("assistant", output_reflect, chat_reflect)
            status = "#" * 50 + " Reflcetion " + "#" * 50
            logger.info(status)
            logger.info(output_reflect)
            logger.info('#' * len(status))
        
            if 'A' in reflect:
                thought_history.append(thought)
                summary_history.append(summary)
                action_history.append(action)
                
                prompt_planning = get_process_prompt(instruction, thought_history, summary_history, action_history, completed_requirements, add_info)
                chat_planning = init_memory_chat()
                chat_planning = add_response("user", prompt_planning, chat_planning)
                output_planning = inference_chat(chat_planning, planning_model, API_url, token, logger, flag.value)
                if(output_planning is None):
                    return
                chat_planning = add_response("assistant", output_planning, chat_planning)
                status = "#" * 50 + " Planning " + "#" * 50
                logger.info(status)
                logger.info(output_planning)
                logger.info('#' * len(status))
                completed_requirements = output_planning.split("### Completed contents ###")[-1].replace("\n", " ").strip()
                
                error_flag = False
            
            elif 'B' in reflect:
                error_flag = True
                back(adb_path)
                
            elif 'C' in reflect:
                error_flag = True
        
        else:
            thought_history.append(thought)
            summary_history.append(summary)
            action_history.append(action)
            
            prompt_planning = get_process_prompt(instruction, thought_history, summary_history, action_history, completed_requirements, add_info)
            chat_planning = init_memory_chat()
            chat_planning = add_response("user", prompt_planning, chat_planning)
            output_planning = inference_chat(chat_planning, planning_model, API_url, token, logger, flag.value)
            if(output_planning is None):
                return
            chat_planning = add_response("assistant", output_planning, chat_planning)
            status = "#" * 50 + " Planning " + "#" * 50
            logger.info(status)
            logger.info(output_planning)
            logger.info('#' * len(status))
            completed_requirements = output_planning.split("### Completed contents ###")[-1].replace("\n", " ").strip()
            
        os.remove(last_screenshot_file)

def get_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--init", action="store_true")
    parser.add_argument("--actions", action="store_true")
    parser.add_argument("--action", type=str)
    parser.add_argument("--prompt", type=str)
    parser.add_argument("--history", action="store_true")
    parser.add_argument("--stop", action="store_true")
    args = parser.parse_args()
    return args


def list_actions():
    print(actions)
    return actions

def open_history():
    current_directory = os.getcwd()
    logs_dir = os.path.join(current_directory, 'logs')

    subprocess.run(f'explorer "{logs_dir}"')

def echo_client(conn, flag, groundingdino_model, ocr_detection):
    try:
        while True:
            msg = conn.recv()
            conn.send(msg)
            #print("内部消息: " + msg)
            if msg == 'stop':
                flag.value = True
            elif msg.startswith('run '):
                flag.value = False
                instruction = msg[4:]
                # print("开始执行：" + instruction)
                do_run(instruction, flag, groundingdino_model, ocr_detection)

    except EOFError:
        print('Connection closed')

# def echo_server(address, authkey, flag):
#     serv = Listener(address, authkey=authkey)
#     while True:
#         try:
#             client = serv.accept()
#             echo_client(client, flag)
#         except Exception:
#             traceback.print_exc()

def clear_process(port):
    r = os.popen("netstat -ano | findstr "+str(port))
    text = r.read()
    arr=text.split("\n")
    #print("进程个数为：",len(arr)-1)
    for text0 in arr:
        arr2=text0.split(" ")
        if len(arr2)>1:
            pid=arr2[len(arr2)-1]
            os.system("taskkill /PID "+pid+" /T /F")
            print(pid)
    r.close()

# def kill_process():
#     r = os.popen("tasklist /FO csv | findstr " + process_name)
#     text = r.read()
#     arr=text.split("\n")
#     #print("进程个数为：",len(arr)-1)
#     for row in csv.reader(arr):
#         print(row)
#         if len(row)>1:
#             pid=row[1]
#             print(pid)
#             os.system("taskkill /PID "+pid+" /T /F")
#     r.close()


def run_action(instruction):
    # clear_process(connection_port)
    print("开始执行指令：" + instruction)
    c = Client(('localhost', connection_port), authkey=b'stop_an_action')
    c.send('run ' + instruction)
    c.recv()

def stop_action():
    print("开始停止运行当前指令")
    c = Client(('localhost', connection_port), authkey=b'stop_an_action')
    c.send('stop')
    c.recv()

def run(args):
    init = args.init
    list_all_actions = args.actions
    action = args.action
    prompt = args.prompt
    history = args.history
    stop = args.stop

    if(init is True):
        return start_server()
    elif(list_all_actions is True):
        return list_actions()
    elif(action is not None):
        for a in actions:
            if(a['action'] == args.action):
                instruction = a['prompt']
                # print(instruction)
                run_action(instruction)
                break
        return
    elif(prompt is not None):
        run_action(prompt)
        return
    elif(history is True):
        open_history()
        return
    elif(stop is True):
        stop_action()
        return

if __name__ == "__main__":
    freeze_support()
    current_directory = os.getcwd()
    logs_dir = os.path.join(current_directory, 'logs')
    if not os.path.exists(logs_dir):
        os.makedirs(logs_dir)
    args = get_args()
    run(args)