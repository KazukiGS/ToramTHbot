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
import threading
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

# คำนำหน้าคำสั่งบอท (Prefix)
COMMAND_PREFIX = "!"

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
# 🚀 START
# ==========================================================
if __name__ == "__main__":
    keep_alive()  # เริ่ม Flask server ก่อน เพื่อให้ Render เห็น port เปิดทันที
    bot.run(DISCORD_TOKEN)
