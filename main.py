"""
==========================================================
 main.py - Discord Bot "บ้านบัฟ" (Buff Houses)
==========================================================
 - ใช้ discord.py สำหรับ Discord Bot
 - ใช้ Flask + Thread สำหรับเปิด Web Server คู่ขนาน
   (จำเป็นสำหรับ Render Web Service ฟรี ที่ต้อง bind port)
 - ใช้ Google Apps Script Web App เป็น "ฐานข้อมูลกลาง"
   ผ่านการยิง HTTP GET / POST ด้วย requests
==========================================================

*** สิ่งที่ต้องแก้ไขก่อนใช้งาน ***
1. YOUR_DISCORD_TOKEN   -> ใส่ Token ของ Discord Bot
2. YOUR_APPS_SCRIPT_URL -> ใส่ URL ของ Web App ที่ deploy จาก Apps Script
   (แนะนำให้ตั้งเป็น Environment Variable บน Render แทนการ hardcode ในไฟล์)
"""

import os
import io
import json
import base64
import threading
from datetime import datetime, timezone, timedelta

import requests
import discord
from discord.ext import commands
from flask import Flask

# ==========================================================
# ⚙️ CONFIG - ตั้งค่าตัวแปรหลักตรงนี้
# ==========================================================
# แนะนำให้ตั้งค่าเป็น Environment Variable บน Render (Settings > Environment)
# แต่ถ้าจะทดสอบเร็วๆ บนเครื่องตัวเอง สามารถใส่ค่าตรงๆ แทน os.getenv(...) ได้
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "YOUR_DISCORD_TOKEN")
APPS_SCRIPT_URL = os.getenv("APPS_SCRIPT_URL", "YOUR_APPS_SCRIPT_URL")

# --- ตั้งค่าสำหรับฟีเจอร์ !scan (OCR อ่านไอเทมด้วย Gemini) ---
# เอา API Key ได้ฟรีจาก Google AI Studio: https://aistudio.google.com/apikey
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "YOUR_GEMINI_API_KEY")

# เลือกรุ่น Gemini ที่จะใช้ (ปรับเปลี่ยนได้ง่ายๆ ผ่าน Environment Variable)
# ตัวเลือกที่แนะนำ (รุ่นประหยัด เหมาะกับงาน OCR ปริมาณมาก):
#   - gemini-3.5-flash-lite  (แนะนำ: แม่นยำกว่า เร็ว ราคายังถูก)
#   - gemini-3.1-flash-lite  (ประหยัดสุด เหมาะงานปริมาณมากๆ)
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

# คำนำหน้าคำสั่งบอท (Prefix)
COMMAND_PREFIX = "!"

# โซนเวลาไทย (UTC+7) สำหรับบันทึก Timestamp ให้อ่านง่าย
THAILAND_TZ = timezone(timedelta(hours=7))

# ==========================================================
# 🌐 FLASK SERVER (สำหรับ Render Web Service)
# ==========================================================
# Render ต้องการให้ Web Service มี Port เปิดรับ HTTP request อยู่เสมอ
# ไม่งั้นจะขึ้น error "failed to bind port" หรือ service timeout
# เราจึงสร้าง Flask server เล็กๆ ไว้ตอบ "Bot is alive" และรันคู่ขนานกับบอท

app = Flask(__name__)


@app.route("/")
def home():
    return "✅ Buff House Discord Bot is running!"


def run_flask():
    # Render จะกำหนด PORT มาให้ผ่าน Environment Variable ชื่อ PORT
    # ถ้าไม่มี (เช่นรันบนเครื่องตัวเอง) จะ fallback เป็น 8080
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)


def keep_alive():
    """รัน Flask server ใน background thread แยกจาก Discord bot"""
    t = threading.Thread(target=run_flask)
    t.daemon = True
    t.start()


# ==========================================================
# 🤖 DISCORD BOT SETUP
# ==========================================================
intents = discord.Intents.default()
intents.message_content = True  # จำเป็นสำหรับอ่านข้อความคำสั่ง (!บัฟบ้าน ...)

bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents, help_command=None)


@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    print("------")


# ==========================================================
# 📌 คำสั่งที่ 1: !บัฟบ้าน <ชื่อบัฟ>
# ==========================================================
# ยิง GET ไปที่ Apps Script -> ดึงข้อมูลทั้งหมด -> กรองตามชื่อบัฟ (ไม่สนตัวพิมพ์เล็ก-ใหญ่)
# -> แสดงผลเป็นตาราง Markdown code block

@bot.command(name="บัฟบ้าน")
async def search_buff_house(ctx, *, buff_name: str = None):
    if not buff_name:
        await ctx.send("⚠️ กรุณาระบุชื่อบัฟ เช่น `!บัฟบ้าน ป้องกัน`")
        return

    # แจ้งสถานะกำลังค้นหา (เผื่อ Apps Script ตอบช้า)
    async with ctx.typing():
        try:
            response = requests.get(APPS_SCRIPT_URL, timeout=15)
            response.raise_for_status()
            result = response.json()
        except requests.exceptions.RequestException as ex:
            await ctx.send(f"❌ เกิดข้อผิดพลาดในการเชื่อมต่อฐานข้อมูล: `{ex}`")
            return
        except ValueError:
            await ctx.send("❌ ไม่สามารถอ่านข้อมูลที่ได้รับจากฐานข้อมูล (รูปแบบ JSON ไม่ถูกต้อง)")
            return

        if result.get("status") != "success":
            await ctx.send(f"❌ ฐานข้อมูลตอบกลับข้อผิดพลาด: `{result.get('message')}`")
            return

        all_rows = result.get("data", [])

        # กรองข้อมูลตามชื่อบัฟ แบบไม่สนใจตัวพิมพ์เล็ก-ใหญ่ และค้นหาแบบ "มีคำนี้อยู่ในชื่อบัฟ"
        keyword = buff_name.strip().lower()
        matched = [row for row in all_rows if keyword in str(row.get("buff", "")).lower()]

        if not matched:
            await ctx.send(f"🔍 ไม่พบข้อมูลบ้านบัฟที่ตรงกับ `{buff_name}`")
            return

        # สร้างตาราง Markdown แบบ monospaced
        # หัวตาราง: Lv. | ชื่อบ้าน | เลขที่บ้าน
        header = f"{'Lv.':<5}{'ชื่อบ้าน':<20}{'เลขที่บ้าน':<15}"
        separator = "-" * len(header)
        lines = [header, separator]

        for row in matched:
            lv = str(row.get("level", "-"))
            name = str(row.get("name", "-"))
            address = str(row.get("address", "-"))
            lines.append(f"{lv:<5}{name:<20}{address:<15}")

        table_text = "\n".join(lines)

        # Discord message limit ~2000 ตัวอักษร, ถ้ายาวเกินไปให้ตัดแบ่งส่ง
        chunk = f"📋 ผลการค้นหาบัฟ: **{buff_name}** (พบ {len(matched)} รายการ)\n```\n{table_text}\n```"
        if len(chunk) > 2000:
            # ตัดส่งเป็นหลายข้อความถ้ายาวเกิน
            await ctx.send(f"📋 ผลการค้นหาบัฟ: **{buff_name}** (พบ {len(matched)} รายการ)")
            current = "```\n" + header + "\n" + separator + "\n"
            for row in matched:
                lv = str(row.get("level", "-"))
                name = str(row.get("name", "-"))
                address = str(row.get("address", "-"))
                line = f"{lv:<5}{name:<20}{address:<15}\n"
                if len(current) + len(line) > 1900:
                    current += "```"
                    await ctx.send(current)
                    current = "```\n" + line
                else:
                    current += line
            current += "```"
            await ctx.send(current)
        else:
            await ctx.send(chunk)


# ==========================================================
# 📌 คำสั่งที่ 2: !เพิ่มบัฟ <ชื่อบัฟ> <เลเวล> <ชื่อบ้าน> <เลขที่บ้าน>
# ==========================================================
# จำกัดสิทธิ์เฉพาะ Administrator เท่านั้น
# ยิง POST ไปที่ Apps Script เพื่อ append ข้อมูลใหม่ลง Google Sheets

@bot.command(name="เพิ่มบัฟ")
@commands.has_permissions(administrator=True)
async def add_buff_house(ctx, buff: str = None, level: str = None, name: str = None, address: str = None):
    # ตรวจสอบว่าใส่ argument ครบทั้ง 4 ตัวหรือไม่
    if not all([buff, level, name, address]):
        await ctx.send(
            "⚠️ รูปแบบคำสั่งไม่ถูกต้อง\n"
            "ใช้งานแบบนี้: `!เพิ่มบัฟ <ชื่อบัฟ> <เลเวล> <ชื่อบ้าน> <เลขที่บ้าน>`\n"
            "ตัวอย่าง: `!เพิ่มบัฟ ป้องกัน 5 บ้านสวย 123/45`"
        )
        return

    payload = {
        "buff": buff,
        "level": level,
        "name": name,
        "address": address,
    }

    async with ctx.typing():
        try:
            response = requests.post(APPS_SCRIPT_URL, json=payload, timeout=15)
            response.raise_for_status()
            result = response.json()
        except requests.exceptions.RequestException as ex:
            await ctx.send(f"❌ เกิดข้อผิดพลาดในการเชื่อมต่อฐานข้อมูล: `{ex}`")
            return
        except ValueError:
            await ctx.send("❌ ไม่สามารถอ่านข้อมูลที่ได้รับจากฐานข้อมูล (รูปแบบ JSON ไม่ถูกต้อง)")
            return

        if result.get("status") == "success":
            await ctx.send(
                f"✅ เพิ่มข้อมูลสำเร็จ!\n"
                f"```\nบัฟ     : {buff}\nเลเวล   : {level}\nชื่อบ้าน : {name}\nเลขที่   : {address}\n```"
            )
        else:
            await ctx.send(f"❌ เพิ่มข้อมูลไม่สำเร็จ: `{result.get('message')}`")


# แจ้งเตือนเมื่อผู้ใช้ที่ไม่มีสิทธิ์ Administrator พยายามใช้คำสั่ง !เพิ่มบัฟ
@add_buff_house.error
async def add_buff_house_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("⛔ คำสั่งนี้ใช้ได้เฉพาะผู้ดูแลระบบ (Administrator) เท่านั้น")
    else:
        await ctx.send(f"❌ เกิดข้อผิดพลาด: `{error}`")


# ==========================================================
# 📌 คำสั่งที่ 3: !scan (แนบรูปมาพร้อมคำสั่ง)
# ==========================================================
# ผู้ใช้พิมพ์ !scan แล้วแนบภาพหน้าจอไอเทมในเกมมาด้วย
# บอทจะส่งภาพไปให้ Gemini อ่าน (OCR) แล้วดึงข้อมูลตามโครงสร้างที่กำหนด
# จากนั้นคำนวณช่วงค่า ATK (สูตรเกม: BaseATK ถึง BaseATK*1.1+10)
# แล้วโชว์ผลลัพธ์พร้อมปุ่ม [✅ ยืนยัน] [✏️ แก้ไข] [❌ ยกเลิก] ให้ผู้ใช้ตรวจสอบก่อนบันทึกจริง
# ถ้ากด "แก้ไข" ผู้ใช้พิมพ์บอกสิ่งที่ผิดเป็นประโยคธรรมชาติ บอทจะส่งให้ Gemini แก้ไขให้ (ไม่ใช้รูปซ้ำ)

# Prompt ที่ใช้สั่ง Gemini ให้อ่านภาพแล้วตอบกลับเป็น JSON เท่านั้น
# กำหนดฟิลด์ให้ชัดเจนตรงกับโครงสร้างที่ต้องการเก็บใน Sheet2
GEMINI_OCR_PROMPT = """
คุณคือระบบ OCR สำหรับอ่านข้อมูลไอเทม/อาวุธจากภาพหน้าจอเกม
จากภาพที่แนบมา ให้ดึงข้อมูลต่อไปนี้ และตอบกลับเป็น JSON เท่านั้น
ห้ามมีคำอธิบายอื่นใดนอกเหนือจาก JSON ห้ามใส่ ```json หรือ Markdown ใดๆ

โครงสร้าง JSON ที่ต้องการ:
{
  "item_name": "ชื่อไอเทม/อาวุธ เต็มๆ ตามที่แสดงในหัวข้อบนสุด",
  "type": "ประเภทของอาวุธ เช่น คาตานะ, ดาบ, ธนู (มักอยู่ในวงเล็บเหลี่ยม เช่น [คาตานะ])",
  "base_atk": ตัวเลข ATK พื้นฐาน (อยู่บรรทัดล่างสุดของภาพ มักเขียนว่า "ATK พื้นฐาน: (ตัวเลข)") ถ้าไม่พบให้ใส่ 0,
  "stability": "ค่าความเสถียร อยู่ในวงเล็บถัดจากค่า ATK บรรทัดบน เช่น (70%) ถ้าไม่พบให้ใส่ค่าว่าง",
  "stats": ["รายการสเตตัส/เอฟเฟกต์ทั้งหมดที่เป็นตัวหนังสือ เช่น ATK+11%, DEX+10%, อาวุธเจาะเข้า+25% - ให้ข้าม/ไม่เอารายการที่มีสัญลักษณ์ไอคอนคริสตัลหรือวงกลมสีนำหน้า (มักเป็นสกิล/เอฟเฟกต์พิเศษ ไม่ใช่ค่าสเตตัสตรง)"]
}

ตัวอย่าง: ถ้าเจอ "ATK พื้นฐาน: (220)" ให้ตอบ "base_atk": 220
ถ้าเจอ "[คาตานะ] ATK: 234 (70%)" ให้ตอบ "type": "คาตานะ" และ "stability": "70%"
"""

# Prompt สำหรับ "แก้ไขข้อมูลตามคำสั่งผู้ใช้" (ไม่มีรูปแล้ว ใช้แค่ข้อความ)
# ส่งข้อมูลเดิม (JSON) + คำสั่งแก้ไขจากผู้ใช้ ให้ Gemini ปรับ JSON ให้ใหม่
GEMINI_CORRECTION_PROMPT_TEMPLATE = """
นี่คือข้อมูลไอเทมที่อ่านได้จาก OCR ก่อนหน้านี้ (รูปแบบ JSON):
{current_json}

ผู้ใช้แจ้งว่าข้อมูลบางส่วนผิด และต้องการแก้ไขดังนี้:
"{correction_text}"

กรุณาปรับปรุงข้อมูล JSON ให้ถูกต้องตามที่ผู้ใช้แจ้ง โดยคงค่าฟิลด์อื่นที่ไม่ได้พูดถึงไว้เหมือนเดิม
ตอบกลับเป็น JSON เท่านั้น ใช้โครงสร้างฟิลด์เดียวกับต้นฉบับทุกประการ:
{{
  "item_name": "...",
  "type": "...",
  "base_atk": ตัวเลข,
  "stability": "...",
  "stats": ["...", "..."]
}}
ห้ามมีคำอธิบายอื่นใดนอกเหนือจาก JSON ห้ามใส่ ```json หรือ Markdown ใดๆ
"""


def call_gemini_ocr(image_bytes: bytes, mime_type: str) -> dict:
    """
    ส่งภาพไปให้ Gemini API อ่านข้อมูล (OCR) แล้วคืนค่าเป็น dict ของ Python
    ใช้ REST API ตรงๆ ผ่าน requests เพื่อไม่ต้องพึ่ง SDK เพิ่มเติม
    """
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")

    parts = [
        {"text": GEMINI_OCR_PROMPT},
        {
            "inline_data": {
                "mime_type": mime_type,
                "data": image_b64,
            }
        },
    ]
    return _call_gemini_generate(parts)


def call_gemini_correction(current_data: dict, correction_text: str) -> dict:
    """
    ส่งข้อมูลเดิม + คำแก้ไขจากผู้ใช้ (เป็นข้อความล้วน ไม่มีรูป) ให้ Gemini ปรับข้อมูลให้ใหม่
    ใช้ตอนผู้ใช้กดปุ่ม "แก้ไข" แล้วพิมพ์บอกว่าอยากแก้อะไร
    """
    current_json_str = json.dumps(current_data, ensure_ascii=False, indent=2)
    prompt = GEMINI_CORRECTION_PROMPT_TEMPLATE.format(
        current_json=current_json_str,
        correction_text=correction_text,
    )
    parts = [{"text": prompt}]
    return _call_gemini_generate(parts)


def _call_gemini_generate(parts: list) -> dict:
    """Helper กลางสำหรับยิง request ไปที่ Gemini API และ parse ผลลัพธ์เป็น JSON"""
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    )

    body = {
        "contents": [{"parts": parts}],
        # บังคับให้ Gemini ตอบกลับเป็น JSON ล้วนๆ (ไม่มี Markdown fence ปน)
        "generationConfig": {
            "response_mime_type": "application/json"
        },
    }

    resp = requests.post(url, json=body, timeout=30)
    resp.raise_for_status()
    result = resp.json()

    # โครงสร้าง response ของ Gemini: candidates[0].content.parts[0].text
    text = result["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(text)


def calculate_atk_range(base_atk) -> str:
    """
    คำนวณช่วงค่า ATK ที่เป็นไปได้ ตามสูตรของเกม:
    ต่ำสุด = BaseATK
    สูงสุด = BaseATK * 1.1 + 10
    คืนค่าเป็น string เช่น "220~252"
    """
    try:
        base = float(base_atk)
        max_atk = round(base * 1.1 + 10)
        return f"{int(base)}~{max_atk}"
    except (ValueError, TypeError):
        return "-"


def build_preview_text(ocr_data: dict) -> str:
    """สร้างข้อความตัวอย่าง (preview) จากข้อมูล OCR เพื่อโชว์ให้ผู้ใช้ตรวจสอบก่อนบันทึก"""
    item_name = ocr_data.get("item_name", "-")
    item_type = ocr_data.get("type", "-")
    base_atk = ocr_data.get("base_atk", 0)
    stability = ocr_data.get("stability", "-")
    stats_list = ocr_data.get("stats", []) or []

    atk_range = calculate_atk_range(base_atk)
    stats_display = "\n".join(f"  • {s}" for s in stats_list) if stats_list else "  -"

    return (
        f"```\n"
        f"ชื่อไอเทม : {item_name}\n"
        f"ประเภท    : {item_type}\n"
        f"BaseATK   : {base_atk}\n"
        f"ATK Range : {atk_range}\n"
        f"Stability : {stability}\n"
        f"สเตตัส:\n{stats_display}\n"
        f"```"
    )


async def save_item_to_sheet(ocr_data: dict, added_by: str) -> dict:
    """ส่งข้อมูลไอเทม (dict) ไปบันทึกที่ Sheet2 (Items) ผ่าน Apps Script คืนค่า result dict"""
    item_name = ocr_data.get("item_name", "-")
    item_type = ocr_data.get("type", "-")
    base_atk = ocr_data.get("base_atk", 0)
    stability = ocr_data.get("stability", "-")
    stats_list = ocr_data.get("stats", []) or []

    atk_range = calculate_atk_range(base_atk)
    stats_text = ", ".join(stats_list) if stats_list else "-"
    timestamp = datetime.now(THAILAND_TZ).strftime("%Y-%m-%d %H:%M:%S")

    payload = {
        "type": "item",
        "itemName": item_name,
        "itemType": item_type,
        "baseAtk": base_atk,
        "atkRange": atk_range,
        "stability": stability,
        "stats": stats_text,
        "addedBy": added_by,
        "timestamp": timestamp,
    }

    response = requests.post(APPS_SCRIPT_URL, json=payload, timeout=15)
    response.raise_for_status()
    return response.json()


class ScanConfirmView(discord.ui.View):
    """
    ปุ่มยืนยัน/แก้ไข/ยกเลิก ที่แนบไปกับข้อความ preview ผลลัพธ์ OCR
    - ✅ ยืนยัน  -> บันทึกข้อมูลลง Google Sheets ทันที
    - ✏️ แก้ไข   -> รอผู้ใช้พิมพ์บอกจุดที่ผิด แล้วส่งให้ Gemini แก้ไข วนโชว์ preview ใหม่อีกรอบ
    - ❌ ยกเลิก  -> ปิดการทำงาน ไม่บันทึกอะไร
    หมดเวลา (timeout) 5 นาที ถ้าไม่มีการกดปุ่ม เพื่อไม่ให้ View ค้างอยู่ตลอดไป
    """

    def __init__(self, ocr_data: dict, author: discord.abc.User):
        super().__init__(timeout=300)
        self.ocr_data = ocr_data
        self.author = author  # เก็บไว้เช็คว่าคนกดปุ่ม/พิมพ์แก้ไข ต้องเป็นคนเดิมที่ !scan

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # อนุญาตเฉพาะผู้ที่สั่ง !scan เท่านั้นให้กดปุ่มได้ (กันคนอื่นมากดสวม)
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(
                "⛔ เฉพาะผู้ที่สั่ง `!scan` เท่านั้นที่กดปุ่มนี้ได้", ephemeral=True
            )
            return False
        return True

    async def disable_all_buttons(self, interaction: discord.Interaction):
        """ปิดการใช้งานปุ่มทั้งหมดหลังจากมีการเลือกแล้ว เพื่อกันการกดซ้ำ"""
        for item in self.children:
            item.disabled = True
        await interaction.message.edit(view=self)

    @discord.ui.button(label="ยืนยัน", style=discord.ButtonStyle.success, emoji="✅")
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await self.disable_all_buttons(interaction)

        try:
            result = await save_item_to_sheet(self.ocr_data, str(self.author))
        except requests.exceptions.RequestException as ex:
            await interaction.followup.send(f"❌ เกิดข้อผิดพลาดในการบันทึกข้อมูล: `{ex}`")
            return

        if result.get("status") == "success":
            await interaction.followup.send(f"✅ บันทึกไอเทมลงฐานข้อมูลสำเร็จ!\n{build_preview_text(self.ocr_data)}")
        else:
            await interaction.followup.send(f"❌ บันทึกข้อมูลไม่สำเร็จ: `{result.get('message')}`")

        self.stop()

    @discord.ui.button(label="แก้ไข", style=discord.ButtonStyle.primary, emoji="✏️")
    async def edit_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.disable_all_buttons(interaction)
        await interaction.response.send_message(
            "✏️ พิมพ์บอกมาได้เลยว่าข้อมูลส่วนไหนผิด และค่าที่ถูกต้องคือะไร\n"
            "เช่น: `BaseATK ที่ถูกต้องคือ 230` หรือ `ชื่อไอเทมผิด ที่ถูกคือ คาตานะครบรอบ 9 ปี VII`\n"
            "(พิมพ์ตอบในแชทนี้ภายใน 3 นาที)"
        )

        def check(m: discord.Message):
            return m.author.id == self.author.id and m.channel.id == interaction.channel.id

        try:
            reply_msg = await bot.wait_for("message", check=check, timeout=180)
        except TimeoutError:
            await interaction.followup.send("⏰ หมดเวลารอการแก้ไข กรุณาพิมพ์ `!scan` ใหม่อีกครั้ง")
            self.stop()
            return

        async with reply_msg.channel.typing():
            try:
                updated_data = call_gemini_correction(self.ocr_data, reply_msg.content)
            except requests.exceptions.RequestException as ex:
                await reply_msg.channel.send(f"❌ เชื่อมต่อ Gemini API ไม่สำเร็จ: `{ex}`")
                self.stop()
                return
            except (KeyError, IndexError, json.JSONDecodeError) as ex:
                await reply_msg.channel.send(f"❌ ไม่สามารถอ่านผลลัพธ์จาก Gemini ได้: `{ex}`")
                self.stop()
                return

        # โชว์ preview ใหม่อีกรอบ พร้อมปุ่มชุดใหม่ (วนแก้ไขได้เรื่อยๆ จนกว่าจะพอใจ)
        new_view = ScanConfirmView(updated_data, self.author)
        await reply_msg.channel.send(
            f"🔄 แก้ไขข้อมูลแล้ว กรุณาตรวจสอบอีกครั้ง:\n{build_preview_text(updated_data)}",
            view=new_view,
        )
        self.stop()

    @discord.ui.button(label="ยกเลิก", style=discord.ButtonStyle.danger, emoji="❌")
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await self.disable_all_buttons(interaction)
        await interaction.followup.send("❌ ยกเลิกแล้ว ไม่มีการบันทึกข้อมูล")
        self.stop()


@bot.command(name="scan")
async def scan_item(ctx):
    # ตรวจสอบว่ามีรูปแนบมาด้วยหรือไม่
    if not ctx.message.attachments:
        await ctx.send("⚠️ กรุณาแนบรูปภาพหน้าจอไอเทมมาพร้อมคำสั่ง `!scan`")
        return

    attachment = ctx.message.attachments[0]

    # ตรวจสอบว่าไฟล์ที่แนบเป็นรูปภาพ
    if not attachment.content_type or not attachment.content_type.startswith("image/"):
        await ctx.send("⚠️ ไฟล์ที่แนบต้องเป็นรูปภาพเท่านั้น (jpg, png ฯลฯ)")
        return

    async with ctx.typing():
        try:
            # ดาวน์โหลดรูปจาก Discord
            image_bytes = await attachment.read()
            mime_type = attachment.content_type

            # เรียก Gemini เพื่ออ่านข้อมูลจากภาพ
            ocr_data = call_gemini_ocr(image_bytes, mime_type)
        except requests.exceptions.RequestException as ex:
            await ctx.send(f"❌ เชื่อมต่อ Gemini API ไม่สำเร็จ: `{ex}`")
            return
        except (KeyError, IndexError, json.JSONDecodeError) as ex:
            await ctx.send(f"❌ ไม่สามารถอ่านผลลัพธ์จาก Gemini ได้ (รูปอาจไม่ชัดหรือไม่ตรงรูปแบบ): `{ex}`")
            return

    # โชว์ผลลัพธ์ preview พร้อมปุ่ม ยืนยัน / แก้ไข / ยกเลิก ให้ผู้ใช้ตรวจสอบก่อนบันทึกจริง
    view = ScanConfirmView(ocr_data, ctx.author)
    await ctx.send(
        f"🔍 อ่านข้อมูลจากภาพได้ดังนี้ กรุณาตรวจสอบก่อนบันทึก:\n{build_preview_text(ocr_data)}",
        view=view,
    )


# ==========================================================
# 🚀 START
# ==========================================================
if __name__ == "__main__":
    keep_alive()  # เริ่ม Flask server ก่อน เพื่อให้ Render เห็น port เปิดทันที
    bot.run(DISCORD_TOKEN)
