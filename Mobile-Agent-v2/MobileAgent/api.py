import base64
import requests

def encode_image(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')


def inference_chat(chat, model, api_url, token, logger, abort_flag):    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}"
    }

    data = {
        "model": model,
        "messages": [],
        "max_tokens": 2048,
        'temperature': 0.0,
        "seed": 1234
    }

    for role, content in chat:
        data["messages"].append({"role": role, "content": content})

    print("inference_chat")
    while True:
        if(abort_flag.value):
            logger.warning('user has aborted this action')
            logger.info('final result: 取消')
            return
        try:
            res = requests.post(api_url, headers=headers, json=data)
            res_json = res.json()
            res_content = res_json['choices'][0]['message']['content']
        except:
            logger.error("Network Error:")
            try:
                logger.info(res.json())
            except:
                logger.error("Request Failed")
        else:
            break
    
    return res_content
