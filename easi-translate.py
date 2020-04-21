#!/usr/bin/env python

import argparse
import glob
import json
import logging
import os
import pinyin
import time
import urllib.parse

from google.cloud import translate
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.common.exceptions import InvalidSessionIdException

LOG_FORMATTER = logging.Formatter("%(asctime)s|%(levelname)s|%(message)s")

ASCII_BUT_UNICODE = [0x2018, 0xff08, 0xff09, 0xff0c]

def is_ascii(s):
    return all(ord(c) < 128 or ord(c) in ASCII_BUT_UNICODE for c in s)

def strip_ascii(s):
    return ''.join(c for c in s if ord(c) > 128 and ord(c) not in ASCII_BUT_UNICODE)

def valid_translate_value(value, key, context):
    if isinstance(value, str):
        if not is_ascii(value):
            return True
    return False

def traverse_json(j, value_processor):
    if isinstance(j, list):
        for i, x in enumerate(j):
            if value_processor(x, i, j):
                yield x, i, j
            yield from traverse_json(x, value_processor)
    elif isinstance(j, dict):
        for k, v in j.items():
            if value_processor(v, k, j):
                yield v, k, j
            yield from traverse_json(v, value_processor)

def get_page(url, xpaths, driver):
    page = False
    results = []

    try:
        driver.get(url)
        page = True
    except Exception as e:
        logging.error(f"Failed to get {url} ({e})")

    if page:
        for xpath, attr in xpaths:
            try:
                el = driver.find_element_by_xpath(xpath)
                results.append(el.get_attribute(attr[1:]) if attr.startswith("@") else getattr(el, attr))
            except InvalidSessionIdException as e:
                return None
            except Exception as e:
                pass

    return results

def main():
    parser = argparse.ArgumentParser(description="EASI Translate")
    parser.add_argument("--menus", required=True, help="Path to Directory containing EASI Menu JSONs")
    parser.add_argument("--webdriver", required=True, help="The type of Selenium WebDriver")
    parser.add_argument("--webdriver-path", required=True, help="Path to the Selenium WebDriver")
    parser.add_argument("--gapps", required=True, help="Path to Google Application Credentials JSON")
    parser.add_argument("--log", required=False, help="Path to log file")
    args = parser.parse_args()

    logger = logging.getLogger()
    console_logger = logging.StreamHandler()
    console_logger.setFormatter(LOG_FORMATTER)
    logger.addHandler(console_logger)
    logger.setLevel(logging.DEBUG)
    if args.log:
        file_logger = logging.FileHandler(args.log)
        file_logger.setFormatter(LOG_FORMATTER)
        logger.addHandler(file_logger)
    
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = args.gapps
    with open(args.gapps, "r") as f:
        gapps_json = json.load(f)

    google_client = translate.TranslationServiceClient()
    google_parent = google_client.location_path(gapps_json["project_id"], "global")

    os.environ["PATH"] += os.pathsep + os.path.dirname(os.path.abspath(args.webdriver_path))
    driver = getattr(webdriver, args.webdriver)()

    shop_infos = { }

    menus = glob.glob(os.path.join(args.menus, "*.json"))
    for i, path in enumerate(menus):
        logging.info(f"Processing {path} ({i + 1}/{len(menus)})")

        with open(path, "r") as f:
            menu_json = json.load(f)

        shop_info = menu_json["data"]["shop_info"]
        shop_infos[shop_info["id"]] = shop_info

        all_translation_values = [ ]
        for value, key, context in traverse_json(menu_json, valid_translate_value):
            all_translation_values.append(value)

        translation_response = None
        try:
            translation_response = google_client.translate_text(
                parent=google_parent,
                contents=all_translation_values,
                mime_type="text/html", 
                source_language_code="zh-cn",
                target_language_code="en"
            )
        except Exception as e:
            logging.error(f"Failed (Google) for {path} ({e})")

        for j, vkc in enumerate(traverse_json(menu_json, valid_translate_value)):
            logging.info(f"{j + 1} / {len(all_translation_values)}")
            
            value, key, context = vkc

            context[key] = { 
                "value": value, 
            }

            pinyin_value = strip_ascii(value).strip()
            if len(pinyin_value) > 0:
                context[key]["pinyin"] = pinyin.get(strip_ascii(value), delimiter=" ")
            
            if "price" in context:
                google_search = None
                while google_search is None:
                    google_search = get_page(f"https://www.google.com/search?q={urllib.parse.quote(value)}", [
                            ("//*[contains(@class,'kno-ecr-pt')]/span", "text"), 
                            ("//*[contains(@class,'kno-ecr-pt')]/following-sibling::node()/span", "text")
                        ], driver)
                    if google_search is None:
                        driver = getattr(webdriver, args.webdriver)()
                
                google_img_search = None
                while google_img_search is None:
                    google_img_search = get_page(f"https://www.google.com/search?tbm=isch&q={urllib.parse.quote(value)}+food", [
                            ("//*[contains(@class,'rg_i')]", "@src")
                        ], driver)
                    if google_search is None:
                        driver = getattr(webdriver, args.webdriver)()

                if len(google_search) > 0:
                    context[key]["knowledge_graph"] = google_search[0]

                if len(google_img_search) > 0:
                    context[key]["google_image"] = google_img_search[0]

                time.sleep(0.5)

            if translation_response and j < len(translation_response.translations):
                context[key]["translation"] = translation_response.translations[j].translated_text
        try:
            with open(f"menu.{os.path.splitext(path)[0]}-processed.json", "w") as f:
                f.write(json.dumps(menu_json, indent=4))
        except Exception as e:
            logging.error(f"Failed (Writing) for {path} ({e})")

    try:
        with open(os.path.join(os.path.dirname(path), "index.json"), "w") as f:
            f.write(json.dumps(shop_infos, indent=4))
    except Exception as e:
        logging.error(f"Failed (Writing) for {path} ({e})")

    driver.close()
        
if __name__ == "__main__":
    main()