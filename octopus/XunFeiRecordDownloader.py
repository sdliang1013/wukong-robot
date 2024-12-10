# 此文件用于批量下载讯飞智能录音笔的转写文件，然后上传到百度文心一言提取问答数据
import requests
import json
import os, time

# 录音转写文件列表接口
listUrl = 'https://huiji.iflyrec.com/XFTJWebAdaptService/v1/transcriptOrders?offset={offset}&limit=20&audioName=&selectType=onlyUnrealTime&types=&t={tick}'
downloadUrl = 'https://www.iflyrec.com/XFTJWebAdaptService/v4/audios/transcriptResults?action=export&fileType=2&showTimeStamp=1&showSpeaker=1&audioIds={audioId}&noParagraph=0&showTranslateResult=0&showFieldNotes=0&t={tick}'
downloadPath = '/home/ivan/孟超录音文件/'

# cookie 需要每次从网页登录后F12获取
listCookie = 'HMACCOUNT=CBB1289D4D2EE104; collectionGuide=true; d_d_ci=55818cf3-c6ea-64c7-bde0-1c77f2dc7028; ui=1802101106384213; Hm_lvt_c711619f2461980a3dbceed5874b0c6a=1725862252; kfId=64a8888205b81f2862ae3327c7bbb333; Hm_lvt_c2eea2fdc6304bf53f0e91cbed44e621=1725932746; X-Biz-Id=huiji; uid=1802101106384213; Hm_lpvt_c711619f2461980a3dbceed5874b0c6a=1728382600; websessionkey=60c369093427426db4f757757c89cf29; sessionkey=4d07657d255d4ec59d105f87df765e07; Hm_lpvt_c2eea2fdc6304bf53f0e91cbed44e621=1728441200; _uetsid=711f1a50852011efb3660bdd43072d06|9t86lr|2|fpv|0|1742; _uetvid=46287ea06e7211ef90fc693a12da5666|1s2fc3e|1728454781034|1|1|bat.bing.com/p/insights/c/v'
downloadCookie = 'HMACCOUNT=CBB1289D4D2EE104; collectionGuide=true; d_d_ci=5475a6c7-8e32-6019-90ca-56ecff54d9c7; miniProgramIsShow=true; Hm_lvt_c711619f2461980a3dbceed5874b0c6a=1725862252; uid=1802101106384213; 2024-%u8BAF%u98DE%u542C%u89C1%u957F%u671F%u6D3B%u52A8%uFF088%u5468%u5E74%u957F%u671F%u7248%u672C%uFF09%u3010%u6539ID%u53EF%u4EE5%uFF0C%u4E0D%u8981%u4E0B%u7EBF%u672C%u6D3B%u52A8%u301115959221162=true; d_d_app_ver=4.3.0; Hm_lpvt_c711619f2461980a3dbceed5874b0c6a=1728382600; websessionkey=60c369093427426db4f757757c89cf29; sessionkey=8cd7d029744840108cd229cc53de4cb6; ui=1802101106384213; _uetsid=711f1a50852011efb3660bdd43072d06|9t86lr|2|fpv|0|1742; daas_st={%22last_config_time%22:1585808307880%2C%22params%22:%22{%5C%22formSubmit%5C%22:%5C%22{%5C%5C%5C%22filter%5C%5C%5C%22:%5C%5C%5C%22%5C%5C%5C%22%2C%5C%5C%5C%22switch%5C%5C%5C%22:%5C%5C%5C%22true%5C%5C%5C%22}%5C%22%2C%5C%22buttonClick%5C%22:%5C%22{%5C%5C%5C%22filter%5C%5C%5C%22:%5C%5C%5C%22%5C%5C%5C%22%2C%5C%5C%5C%22switch%5C%5C%5C%22:%5C%5C%5C%22true%5C%5C%5C%22}%5C%22%2C%5C%22linkClick%5C%22:%5C%22{%5C%5C%5C%22filter%5C%5C%5C%22:%5C%5C%5C%22%5C%5C%5C%22%2C%5C%5C%5C%22switch%5C%5C%5C%22:%5C%5C%5C%22true%5C%5C%5C%22}%5C%22}%22%2C%22sdk_ver%22:%221.3.9%22%2C%22status%22:%221%22}; appid=05298da96e; _uetvid=46287ea06e7211ef90fc693a12da5666|1s2fc3e|1728455182376|2|1|bat.bing.com/p/insights/c/v'

def tick():
    return time.time_ns() // 1000000  # 将纳秒转换为毫秒

def downloadFile(audio_id, audio_title):
    headers = {
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8,ja;q=0.7',
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive',
        'Cookie': downloadCookie,
        'Pragma': 'no-cache',
        'Referer': 'https://www.iflyrec.com/views/html/editor.html?id=PAmz240925093067182BDB0000B',
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'same-origin',
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36',
        'X-Biz-Id': 'xftj',
        'sec-ch-ua': '"Not)A;Brand";v="99", "Google Chrome";v="127", "Chromium";v="127"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"macOS"'
    }
        # 发送GET请求
    response = requests.get(downloadUrl.format(audioId=audio_id, tick=tick()), headers=headers)
    # 将响应内容保存为文件
    file_name = audio_title.replace(":", "_") + '.docx'  # 可以根据需要更改文件名和扩展名
    with open(os.path.join(downloadPath, file_name), 'wb') as f:
        f.write(response.content)
    print(f"文件已下载并保存为 {file_name}")

def listFiles():
    headers = {
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8,ja;q=0.7',
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive',
        'Cookie': listCookie,
        'Pragma': 'no-cache',
        'Referer': 'https://huiji.iflyrec.com/list',
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'same-origin',
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36',
        'X-Biz-Id': 'huiji',
        'sec-ch-ua': '"Not)A;Brand";v="99", "Google Chrome";v="127", "Chromium";v="127"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"macOS"'
    }

    # 发起初始请求，获取总记录数 count
    offset = 0

    # 获取总记录数
    total_count = 1
    # 遍历分页
    while offset < total_count:
        # 发起分页请求
        response = requests.get(listUrl.format(offset=offset, tick=tick()), headers=headers)
        data = response.json()

        if data['code'] == '000000':
            total_count = data['biz']['count']
            print(f"Total records: {total_count}")
            # 获取 orderList 并遍历
            for order in data['biz']['orderList']:
                for audio in order['audioList']:
                    audio_id = audio['audioId']
                    audio_title = audio['audioTitle']
                    print(f"Audio ID: {audio_id}, Audio Title: {audio_title}")
                    downloadFile(audio_id, audio_title)
        else:
            print(f"Error: {data['desc']}")
            return

        # 更新 offset，准备请求下一页
        offset += 20

listFiles()