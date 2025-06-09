# Savie - Seu Assistente Financeiro Pessoal
# Versão 12.4 - FINAL COMPLETO E FUNCIONAL

import logging, os, re, sqlite3, json, asyncio, locale, io, csv
from datetime import datetime, date, timedelta
from calendar import monthrange
from decimal import Decimal, InvalidOperation
from telegram import (Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton)
from telegram.ext import (Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters)
from telegram.constants import ParseMode
import google.generativeai as genai

# --- Configurações Iniciais ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

try:
    locale.setlocale(locale.LC_ALL, 'pt_BR.UTF-8')
except locale.Error:
    try:
        locale.setlocale(locale.LC_ALL, 'Portuguese_Brazil.1252')
    except locale.Error:
        logger.warning("Locale 'pt_BR' não encontrado. Nomes de meses podem aparecer em inglês.")

# --- Constantes e Chaves de API ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_PATH = os.getenv("DB_PATH", "savie_bot.db")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
ADMIN_ID = 1812811739  # ID de Administrador configurado.

if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)
else:
    logger.warning("Chave de API do Google não encontrada. A IA está desativada.")

# --- Constantes de Callback e Estado ---
CALLBACK_CONFIRM_EXPENSE = "confirm_exp"; CALLBACK_CONFIRM_INSTALLMENT = "confirm_inst"; CALLBACK_CANCEL = "cancel_op"
CALLBACK_DELETE_MENU_LAST = "del_menu_last"; CALLBACK_DELETE_MENU_ALL = "del_menu_all"; CALLBACK_DELETE_CONFIRM_LAST = "del_conf_last"; CALLBACK_DELETE_CONFIRM_ALL = "del_conf_all"
CALLBACK_ADD_RECURRING = "add_recur"; CALLBACK_CHALLENGE_ACCEPT = "chall_accept"; CALLBACK_PAY_BILL = "pay_bill"
STATE_ASKING_NAME = "state_ask_name"
STATE_ASKING_EMAIL = "state_ask_email"

def add_months(source_date: date, months: int) -> date:
    month = source_date.month - 1 + months; year = source_date.year + month // 12; month = month % 12 + 1
    day = min(source_date.day, monthrange(year, month)[1]); return date(year, month, day)

# --- Classe Principal do Bot ---
class SavieBot:
    def __init__(self, db_path: str):
        logger.info(f"--- INICIANDO CONEXÃO COM BANCO DE DADOS ---")
        logger.info(f"Caminho do DB fornecido: {db_path}")
        try:
            self.conn = sqlite3.connect(db_path, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute('PRAGMA journal_mode=WAL')
            logger.info("Conexão com SQLite estabelecida com sucesso.")
            logger.info("Iniciando a configuração do banco de dados (chamando setup_database)...")
            self.setup_database()
            logger.info("Configuração do banco de dados (setup_database) CONCLUÍDA.")
        except Exception as e:
            logger.critical(f"--- ERRO CRÍTICO AO INICIAR O BANCO DE DADOS ---: {e}", exc_info=True)

    def setup_database(self):
        logger.info("Dentro de setup_database. Tentando executar CREATE TABLE...")
        try:
            with self.conn:
                cursor = self.conn.cursor()
                cursor.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, full_name TEXT, email TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
                cursor.execute('CREATE TABLE IF NOT EXISTS expenses (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, amount DECIMAL(10,2) NOT NULL, description TEXT NOT NULL, category TEXT NOT NULL, date DATE NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, is_installment BOOLEAN DEFAULT FALSE, installment_id INTEGER, FOREIGN KEY (user_id) REFERENCES users (user_id))')
                cursor.execute('CREATE TABLE IF NOT EXISTS installments (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, total_amount DECIMAL(10,2) NOT NULL, description TEXT NOT NULL, category TEXT NOT NULL, total_installments INTEGER NOT NULL, start_date DATE NOT NULL, FOREIGN KEY (user_id) REFERENCES users (user_id))')
                cursor.execute('CREATE TABLE IF NOT EXISTS categories (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, keywords TEXT NOT NULL, emoji TEXT NOT NULL)')
                cursor.execute('CREATE TABLE IF NOT EXISTS recurring_expenses (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, description TEXT NOT NULL, amount DECIMAL(10,2) NOT NULL, category TEXT NOT NULL, day_of_month INTEGER NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (user_id) REFERENCES users (user_id), UNIQUE(user_id, description))')
                cursor.execute('CREATE TABLE IF NOT EXISTS challenges (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, challenge_type TEXT NOT NULL, target_category TEXT, start_date DATE NOT NULL, end_date DATE NOT NULL, status TEXT NOT NULL, FOREIGN KEY (user_id) REFERENCES users (user_id))')
                cursor.execute('CREATE TABLE IF NOT EXISTS shared_bills (id INTEGER PRIMARY KEY AUTOINCREMENT, creator_user_id INTEGER NOT NULL, creator_username TEXT, group_chat_id INTEGER NOT NULL, summary_message_id INTEGER, description TEXT NOT NULL, total_amount DECIMAL(10,2) NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, status TEXT DEFAULT "open")')
                cursor.execute('CREATE TABLE IF NOT EXISTS bill_participants (id INTEGER PRIMARY KEY AUTOINCREMENT, bill_id INTEGER NOT NULL, participant_user_id INTEGER, participant_username TEXT NOT NULL, amount_due DECIMAL(10,2) NOT NULL, status TEXT DEFAULT "pending", FOREIGN KEY (bill_id) REFERENCES shared_bills (id) ON DELETE CASCADE)')
            self.conn.commit()
            logger.info("Comandos CREATE TABLE executados e commitados com sucesso!")
            logger.info("Iniciando populate_default_categories...")
            self.populate_default_categories()
            logger.info("populate_default_categories concluído.")
        except Exception as e:
            logger.error(f"--- ERRO DENTRO DE setup_database ---: {e}", exc_info=True)

    def populate_default_categories(self):
        with self.conn:
            cursor = self.conn.cursor(); cursor.execute("SELECT COUNT(*) FROM categories")
            if cursor.fetchone()[0] > 0: return
            default_categories = [("Alimentação", "restaurante,lanche,comida,pizza,mercado,ifood,rappi", "🍽️"),("Transporte", "uber,99,táxi,combustivel,gasolina,ônibus,metro,passagem", "🚗"),("Moradia", "aluguel,condomínio,luz,água,gás,internet,iptu", "🏠"),("Saúde", "farmácia,médico,hospital,consulta,remédio,exame,plano", "🏥"),("Lazer", "cinema,show,festa,bar,viagem,streaming,netflix,spotify", "🎉"),("Educação", "curso,livro,escola,faculdade,universidade", "📚"),("Compras", "roupa,sapato,celular,computador,eletrônico,presente", "🛍️"),("Serviços", "salão,barbeiro,manicure,lavanderia,academia,petshop", "🛠️"),("Outros", "imposto,taxa,doação,diversos", "📦")]
            cursor.executemany('INSERT INTO categories (name, keywords, emoji) VALUES (?, ?, ?)', default_categories)
        self.conn.commit()

    def register_user(self, user_id: int, username: str, first_name: str):
        with self.conn:
            self.conn.execute('INSERT OR IGNORE INTO users (user_id, username, first_name) VALUES (?, ?, ?)', (user_id, username, first_name))
            self.conn.execute('UPDATE users SET username = ?, first_name = ? WHERE user_id = ?', (username, first_name, user_id))
        self.conn.commit()

    def get_user_profile(self, user_id: int):
        with self.conn:
            cursor = self.conn.cursor()
            cursor.execute("SELECT full_name, email FROM users WHERE user_id = ?", (user_id,))
            return cursor.fetchone()

    def update_user_profile(self, user_id: int, full_name: str, email: str):
        with self.conn:
            self.conn.execute("UPDATE users SET full_name = ?, email = ? WHERE user_id = ?", (full_name, email, user_id))
            self.conn.commit()

    def create_shared_bill(self, creator_user_id, creator_username, group_chat_id, description, total_amount):
        with self.conn:
            cursor = self.conn.cursor()
            cursor.execute("INSERT INTO shared_bills (creator_user_id, creator_username, group_chat_id, description, total_amount) VALUES (?, ?, ?, ?, ?)", (creator_user_id, creator_username, group_chat_id, description, str(total_amount)))
            self.conn.commit()
            return cursor.lastrowid
            
    def add_bill_participant(self, bill_id, username, amount_due):
        user = self.get_user_by_username(username)
        user_id = user['user_id'] if user else None
        with self.conn:
            cursor = self.conn.cursor()
            cursor.execute("INSERT INTO bill_participants (bill_id, participant_user_id, participant_username, amount_due) VALUES (?, ?, ?, ?)", (bill_id, user_id, username, str(amount_due)))
            self.conn.commit()
            return cursor.lastrowid

    def update_bill_summary_message(self, bill_id, message_id):
        with self.conn: self.conn.execute("UPDATE shared_bills SET summary_message_id = ? WHERE id = ?", (message_id, bill_id)); self.conn.commit()

    def get_user_by_username(self, username):
        cursor = self.conn.cursor(); cursor.execute("SELECT user_id FROM users WHERE username = ?", (username,)); return cursor.fetchone()

    def mark_participant_as_paid(self, participant_id, payer_user_id):
        with self.conn:
            cursor = self.conn.cursor()
            cursor.execute("UPDATE bill_participants SET status = 'paid' WHERE id = ? AND (participant_user_id = ? OR participant_user_id IS NULL)", (participant_id, payer_user_id))
            cursor.execute("UPDATE bill_participants SET participant_user_id = ? WHERE id = ? AND participant_user_id IS NULL", (payer_user_id, participant_id))
            self.conn.commit()
            cursor.execute("SELECT bill_id FROM bill_participants WHERE id = ?", (participant_id,)); return cursor.fetchone()['bill_id']

    def get_bill_status(self, bill_id):
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM shared_bills WHERE id = ?", (bill_id,)); bill_info = cursor.fetchone()
        cursor.execute("SELECT participant_username, status FROM bill_participants WHERE bill_id = ?", (bill_id,)); participants = cursor.fetchall()
        return bill_info, participants
    
    def parse_expense_text(self, text: str) -> dict | None:
        match = re.search(r'(\d[\d.,]*)', text);
        if not match: return None
        amount_str = match.group(1).replace('.', '').replace(',', '.');
        try: amount = Decimal(amount_str)
        except InvalidOperation: return None
        desc = re.sub(r'(\d[\d.,]*)', '', text, 1); desc = re.sub(r'\b(gastei|comprei|paguei|valor|preço|reais|r\$)\b', '', desc, flags=re.I);
        desc = ' '.join(desc.split()).strip().capitalize(); return {'amount': amount, 'description': desc or "Gasto não especificado"}

    def categorize_expense(self, description: str) -> str:
        desc_lower = description.lower(); cursor = self.conn.cursor()
        cursor.execute('SELECT name, keywords, emoji FROM categories')
        all_categories = cursor.fetchall()
        for cat in all_categories:
            if any(keyword.strip() in desc_lower for keyword in cat['keywords'].split(',')): logger.info(f"Gasto '{description}' categorizado por palavra-chave como '{cat['name']}'."); return f"{cat['emoji']} {cat['name']}"
        if GOOGLE_API_KEY:
            logger.info(f"Nenhuma palavra-chave encontrada para '{description}'. Usando IA...")
            category_names = [cat['name'] for cat in all_categories]
            try:
                prompt = f"Você é um assistente de finanças. Sua tarefa é categorizar a despesa em uma das seguintes categorias: {', '.join(category_names)}. Responda APENAS com um objeto JSON no formato: {{\"categoria\": \"Nome da Categoria\"}}\n\nDescrição da despesa: \"{description}\""
                model = genai.GenerativeModel('gemini-1.5-flash-latest'); response = model.generate_content(prompt); json_text = response.text.strip().replace("```json", "").replace("```", ""); ai_result = json.loads(json_text)
                ai_category_name = ai_result.get("categoria")
                for cat in all_categories:
                    if cat['name'] == ai_category_name: logger.info(f"IA categorizou '{description}' como '{ai_category_name}'."); return f"{cat['emoji']} {cat['name']}"
            except Exception as e: logger.error(f"Erro ao categorizar com IA: {e}")
        logger.warning(f"Não foi possível categorizar '{description}'. Usando 'Outros'."); return "📦 Outros"

    def add_expense(self, user_id: int, amount: Decimal, desc: str, cat: str, p_date: date, inst_id: int = None):
        with self.conn: self.conn.execute('INSERT INTO expenses (user_id, amount, description, category, date, is_installment, installment_id) VALUES (?, ?, ?, ?, ?, ?, ?)',(user_id, str(amount), desc, cat, p_date, inst_id is not None, inst_id)); self.conn.commit()

    def add_installment_purchase(self, user_id: int, total_amount: Decimal, desc: str, cat: str, count: int, start_date: date):
        inst_amount = total_amount / Decimal(count)
        with self.conn:
            cursor = self.conn.cursor(); cursor.execute('INSERT INTO installments (user_id, total_amount, description, category, total_installments, start_date) VALUES (?, ?, ?, ?, ?, ?)',(user_id, str(total_amount), desc, cat, count, start_date)); installment_id = cursor.lastrowid
            for i in range(count): self.add_expense(user_id, inst_amount, f"{desc} ({i+1}/{count})", cat, add_months(start_date, i), installment_id)
        self.conn.commit()

    def get_monthly_summary(self, user_id: int):
        first_day = date.today().replace(day=1); cursor = self.conn.cursor()
        cursor.execute("SELECT SUM(amount) FROM expenses WHERE user_id = ? AND date >= ?", (user_id, first_day)); total = cursor.fetchone()[0]
        if not total: return None
        cursor.execute("SELECT category, SUM(amount) as cat_total FROM expenses WHERE user_id = ? AND date >= ? GROUP BY category ORDER BY cat_total DESC", (user_id, first_day)); by_category = cursor.fetchall()
        return {'total': Decimal(total), 'by_category': by_category}

    def get_last_expense(self, user_id: int):
        cursor = self.conn.cursor(); query = "SELECT id, description, amount, category, date FROM expenses WHERE user_id = ? ORDER BY id DESC LIMIT 1"
        cursor.execute(query, (user_id,)); return cursor.fetchone()

    def delete_expense_by_id(self, expense_id: int, user_id: int):
        with self.conn: self.conn.execute("DELETE FROM expenses WHERE id = ? AND user_id = ?", (expense_id, user_id)); self.conn.commit()

    def delete_all_user_data(self, user_id: int):
        with self.conn:
            self.conn.execute("DELETE FROM installments WHERE user_id = ?", (user_id,)); self.conn.execute("DELETE FROM recurring_expenses WHERE user_id = ?", (user_id,)); self.conn.execute("DELETE FROM challenges WHERE user_id = ?", (user_id,)); self.conn.execute("DELETE FROM expenses WHERE user_id = ?", (user_id,)); self.conn.commit()

    def get_spending_analytics(self, user_id: int, category: str):
        cursor = self.conn.cursor(); query_current = "SELECT SUM(amount) FROM expenses WHERE user_id = ? AND category = ? AND strftime('%Y-%m', date) = strftime('%Y-%m', 'now', 'localtime')"; cursor.execute(query_current, (user_id, category)); current_month_total = cursor.fetchone()[0] or 0; query_avg = "SELECT AVG(monthly_total) FROM (SELECT SUM(amount) as monthly_total FROM expenses WHERE user_id = ? AND category = ? AND strftime('%Y-%m', date) != strftime('%Y-%m', 'now', 'localtime') GROUP BY strftime('%Y-%m', date))"; cursor.execute(query_avg, (user_id, category)); historical_avg = cursor.fetchone()[0]
        return {"current_total": Decimal(current_month_total), "historical_avg": Decimal(historical_avg) if historical_avg else Decimal(0)}

    def find_recurring_pattern(self, user_id: int, description: str, amount: Decimal):
        cursor = self.conn.cursor(); amount_min = amount * Decimal('0.95'); amount_max = amount * Decimal('1.05'); cursor.execute("SELECT 1 FROM recurring_expenses WHERE user_id = ? AND description = ?", (user_id, description))
        if cursor.fetchone(): return False
        query = "SELECT COUNT(DISTINCT strftime('%Y-%m', date)) FROM expenses WHERE user_id = ? AND description = ? AND amount BETWEEN ? AND ? AND date >= date('now', '-3 months', 'localtime')"
        cursor.execute(query, (user_id, description, str(amount_min), str(amount_max))); months_count = cursor.fetchone()[0]
        return months_count >= 2

    def add_recurring_expense(self, user_id: int, day_of_month: int, pending_expense: dict):
        with self.conn: self.conn.execute("INSERT OR IGNORE INTO recurring_expenses (user_id, description, amount, category, day_of_month) VALUES (?, ?, ?, ?, ?)", (user_id, pending_expense['desc'], str(pending_expense['amount']), pending_expense['category'], day_of_month)); self.conn.commit()

    def process_due_subscriptions(self):
        today = date.today(); cursor = self.conn.cursor(); query_due = "SELECT * FROM recurring_expenses WHERE day_of_month = ?"; cursor.execute(query_due, (today.day,))
        for sub in cursor.fetchall():
            user_id, desc, amount, category = sub['user_id'], sub['description'], sub['amount'], sub['category']; query_exists = "SELECT 1 FROM expenses WHERE user_id = ? AND description = ? AND strftime('%Y-%m', date) = strftime('%Y-%m', 'now', 'localtime')"; cursor.execute(query_exists, (user_id, desc))
            if not cursor.fetchone(): logger.info(f"Lançando assinatura vencida para user {user_id}: {desc}"); self.add_expense(user_id, Decimal(amount), desc, category, today)

    def start_no_spend_challenge(self, user_id: int, category: str, duration_days: int):
        start_date = date.today(); end_date = start_date + timedelta(days=duration_days)
        with self.conn: self.conn.execute("UPDATE challenges SET status = 'cancelled' WHERE user_id = ? AND status = 'active'", (user_id,)); self.conn.execute("INSERT INTO challenges (user_id, challenge_type, target_category, start_date, end_date, status) VALUES (?, ?, ?, ?, ?, ?)",(user_id, 'no_spend', category, start_date, end_date, 'active')); self.conn.commit()

    def check_challenge_violation(self, user_id: int, category: str) -> bool:
        cursor = self.conn.cursor(); query = "SELECT id FROM challenges WHERE user_id = ? AND challenge_type = 'no_spend' AND target_category = ? AND status = 'active' AND date('now', 'localtime') <= end_date"; cursor.execute(query, (user_id, category)); active_challenge = cursor.fetchone()
        if active_challenge:
            with self.conn: self.conn.execute("UPDATE challenges SET status = 'failed' WHERE id = ?", (active_challenge['id'],)); self.conn.commit()
            return True
        return False

    def check_completed_challenges(self) -> list:
        cursor = self.conn.cursor(); query = "SELECT id, user_id, target_category FROM challenges WHERE status = 'active' AND end_date < date('now', 'localtime')"; cursor.execute(query); completed = cursor.fetchall()
        if completed:
            completed_ids = [c['id'] for c in completed];
            with self.conn: self.conn.execute(f"UPDATE challenges SET status = 'completed' WHERE id IN ({','.join('?' for _ in completed_ids)})", completed_ids); self.conn.commit()
        return completed
        
    def get_active_installments(self, user_id: int) -> list:
        cursor = self.conn.cursor()
        query = """
            SELECT i.description, i.total_amount, i.total_installments,
                   (SELECT COUNT(*) FROM expenses WHERE installment_id = i.id) as paid_count
            FROM installments i
            WHERE i.user_id = ? AND
                  (SELECT COUNT(*) FROM expenses WHERE installment_id = i.id) < i.total_installments
            ORDER BY i.start_date DESC
        """
        cursor.execute(query, (user_id,))
        return cursor.fetchall()

# Instância do Bot
savie = SavieBot(db_path=DB_PATH)

# --- Funções Handler e Lógica da IA ---

async def generate_natural_response(text: str, user_name: str) -> str:
    """Usa a IA Generativa para criar uma resposta curta e natural."""
    if not GOOGLE_API_KEY:
        return "Entendido!"
    try:
        logger.info(f"Gerando resposta natural de IA para: '{text}'")
        prompt = (
            f"Você é Savie, um assistente financeiro amigável e prestativo. "
            f"Um usuário chamado {user_name} te enviou a seguinte mensagem, que não é um comando nem um registro de gasto: '{text}'. "
            f"Sua tarefa é responder de forma curta, positiva e natural, como um humano faria. "
            f"Se for um agradecimento, agradeça de volta. Se for uma afirmação, confirme com um 'Ok!' ou 'Combinado!'. "
            f"Não faça perguntas. Mantenha a resposta com no máximo 10 palavras."
        )
        model = genai.GenerativeModel('gemini-1.5-flash-latest')
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        logger.error(f"Erro ao gerar resposta com IA: {e}")
        return "Ok!"

async def gatekeeper(update: Update, context: ContextTypes.DEFAULT_TYPE, command_name: str) -> bool:
    user_id = update.effective_user.id
    profile = savie.get_user_profile(user_id)
    if profile and profile['full_name'] and profile['email']:
        return True
    logger.info(f"Usuário não cadastrado {user_id} tentou usar o comando '{command_name}'. Forçando cadastro.")
    if command_name != "start":
        await update.message.reply_text("Para usar qualquer comando, você precisa primeiro completar seu cadastro.")
    await start(update, context, force_register=True)
    return False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE, force_register: bool = False):
    user = update.effective_user
    savie.register_user(user.id, user.username, user.first_name)
    profile = savie.get_user_profile(user.id)
    if not force_register and (profile and profile['full_name'] and profile['email']):
        welcome_text = (f"👋 *Olá de novo, {profile['full_name'].split()[0]}!* Que bom te ver.\n\n"
                        "Use os botões abaixo ou me envie um gasto para começar.")
        keyboard = [[KeyboardButton("📊 Gastos do Mês"), KeyboardButton("📈 Por Categoria")], [KeyboardButton("💳 Ver Parcelas"), KeyboardButton("❓ Ajuda")], [KeyboardButton("🎯 Desafios"), KeyboardButton("🗑️ Excluir Dados")]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        await update.message.reply_text(welcome_text, parse_mode='Markdown', reply_markup=reply_markup)
    else:
        await update.message.reply_text(
            f"👋 *Olá, {user.first_name}! Eu sou o Savie, seu assistente financeiro.*\n\n"
            "Para começar, preciso que complete seu cadastro. Por favor, digite seu *nome completo*.",
            parse_mode='Markdown'
        )
        context.user_data['state'] = STATE_ASKING_NAME

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    state = context.user_data.get('state')

    if state == STATE_ASKING_NAME:
        full_name = text.strip()
        if len(full_name.split()) < 2:
             await update.message.reply_text("Por favor, digite seu nome e sobrenome.")
             return
        context.user_data['full_name'] = full_name
        await update.message.reply_text(
            f"Obrigado, {full_name.split()[0]}! Agora, por favor, digite seu *melhor e-mail*.",
            parse_mode='Markdown'
        )
        context.user_data['state'] = STATE_ASKING_EMAIL
        return

    if state == STATE_ASKING_EMAIL:
        email = text.lower().strip()
        if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
            await update.message.reply_text("🤔 Hmm, este e-mail não parece válido. Por favor, tente novamente.")
            return
        full_name = context.user_data.get('full_name')
        savie.update_user_profile(user_id, full_name, email)
        context.user_data.clear()
        keyboard = [[KeyboardButton("📊 Gastos do Mês"), KeyboardButton("📈 Por Categoria")], [KeyboardButton("💳 Ver Parcelas"), KeyboardButton("❓ Ajuda")], [KeyboardButton("🎯 Desafios"), KeyboardButton("🗑️ Excluir Dados")]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        await update.message.reply_text(
            "✅ *Cadastro concluído com sucesso!*\n\n"
            "Agora sim! Você já pode usar todas as minhas funcionalidades. Tente me enviar um gasto, como `Café 10,50`.",
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
        return

    profile = savie.get_user_profile(user_id)
    if not (profile and profile['full_name'] and profile['email']):
        await start(update, context, force_register=True)
        return

    if text in ["📊 Gastos do Mês", "📈 Por Categoria", "💳 Ver Parcelas", "❓ Ajuda", "🎯 Desafios", "🗑️ Excluir Dados"]:
        await handle_keyboard_buttons(update, context)
        return

    installment_match = re.search(r'(\d+)\s*x', text, re.I) or re.search(r'parcelado em\s*(\d+)', text, re.I)
    if installment_match:
        await process_installment_text(update, context, installment_match)
    else:
        parsed_data = savie.parse_expense_text(text)
        if parsed_data:
            await process_single_expense_text(update, context)
        else:
            resposta_ia = await generate_natural_response(text, update.effective_user.first_name)
            await update.message.reply_text(resposta_ia)

async def check_for_anomalies_and_patterns(user_id: int, expense: dict, context: ContextTypes.DEFAULT_TYPE):
    if savie.check_challenge_violation(user_id, expense['category']):
        await context.bot.send_message(chat_id=user_id, text=f"Ah, não! 😟\nVocê registrou um gasto na categoria *{expense['category']}* e quebrou seu desafio atual. Mas não desanime, você pode começar um novo com o comando /desafio!", parse_mode='Markdown'); return
    analytics = savie.get_spending_analytics(user_id, expense['category'])
    if analytics['historical_avg'] > 0:
        today = date.today(); days_in_month = monthrange(today.year, today.month)[1]; month_progress = today.day / days_in_month; spending_progress = analytics['current_total'] / analytics['historical_avg']
        if spending_progress > month_progress + 0.3:
            alert_text = (f"📡 *Radar Savie:* Atenção! Seus gastos com *{expense['category']}* este mês (R$ {analytics['current_total']:.2f}) já representam {spending_progress:.0%} da sua média mensal, mas estamos em {month_progress:.0%} do mês.")
            await context.bot.send_message(chat_id=user_id, text=alert_text, parse_mode='Markdown')
    if savie.find_recurring_pattern(user_id, expense['desc'], expense['amount']):
        suggestion_text = (f"🕵️‍♂️ *Detetive Savie:* Percebi que o gasto '{expense['desc']}' tem se repetido. Deseja que eu o registre como uma despesa recorrente automática todo mês?")
        keyboard = [[InlineKeyboardButton("Sim, criar recorrência", callback_data=CALLBACK_ADD_RECURRING), InlineKeyboardButton("Não, obrigado", callback_data=CALLBACK_CANCEL)]]
        context.user_data['suggestion_for_recurring'] = expense
        await context.bot.send_message(chat_id=user_id, text=suggestion_text, reply_markup=InlineKeyboardMarkup(keyboard))

async def daily_scheduler_job(context: ContextTypes.DEFAULT_TYPE):
    logger.info("Scheduler: Executando tarefas diárias...")
    try:
        savie.process_due_subscriptions()
        completed_challenges = savie.check_completed_challenges()
        for challenge in completed_challenges:
            await context.bot.send_message(chat_id=challenge['user_id'], text=f"🏆 Parabéns! Você completou com sucesso o desafio de não gastar em *{challenge['target_category']}*! Continue assim!", parse_mode='Markdown')
    except Exception as e: logger.error(f"Scheduler: Erro ao executar tarefas diárias: {e}")

async def process_single_expense_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parsed_data = savie.parse_expense_text(update.message.text)
    if not parsed_data or parsed_data['amount'] <= 0: await update.message.reply_text("😕 Desculpe, não consegui entender o valor. Tente algo como: `Lanche 25,50`"); return
    amount, desc = parsed_data['amount'], parsed_data['description']; category = savie.categorize_expense(desc)
    context.user_data['pending_expense'] = {'amount': amount, 'desc': desc, 'category': category}
    preview_text = f"✅ *Gasto reconhecido!*\n\n💵 *Valor:* R$ {amount:.2f}\n📝 *Descrição:* {desc}\n🏷️ *Categoria:* {category}\n\nPosso confirmar?"; keyboard = [[InlineKeyboardButton("👍 Confirmar", callback_data=CALLBACK_CONFIRM_EXPENSE), InlineKeyboardButton("❌ Cancelar", callback_data=CALLBACK_CANCEL)]]
    await update.message.reply_text(preview_text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

async def process_installment_text(update: Update, context: ContextTypes.DEFAULT_TYPE, match: re.Match):
    parsed_data = savie.parse_expense_text(update.message.text)
    if not parsed_data or parsed_data['amount'] <= 0: await update.message.reply_text("😕 Não entendi os detalhes. Tente: `Notebook 3000 em 10x`"); return
    total_amount, desc = parsed_data['amount'], parsed_data['description']; installments_count = int(match.group(1))
    if installments_count <= 1: return await process_single_expense_text(update, context)
    installment_value = total_amount / Decimal(installments_count); category = savie.categorize_expense(desc)
    context.user_data['pending_installment'] = {'total_amount': total_amount, 'desc': desc, 'category': category, 'count': installments_count}
    preview_text = (f"💳 *Parcelamento reconhecido!*\n\n🛍️ *Descrição:* {desc}\n💰 *Valor Total:* R$ {total_amount:.2f}\n📅 *Parcelas:* {installments_count}x de R$ {installment_value:.2f}\n🏷️ *Categoria:* {category}\n\nConfirma o registro?")
    keyboard = [[InlineKeyboardButton("👍 Confirmar", callback_data=CALLBACK_CONFIRM_INSTALLMENT), InlineKeyboardButton("❌ Cancelar", callback_data=CALLBACK_CANCEL)]]
    await update.message.reply_text(preview_text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer(); user_id = query.from_user.id
    profile = savie.get_user_profile(user_id)
    if not (profile and profile['full_name'] and profile['email']):
        await query.edit_message_text("Por favor, complete seu cadastro primeiro. Envie /start.")
        return
    parts = query.data.split('|', 1); action = parts[0]; payload = parts[1] if len(parts) > 1 else None
    try:
        if action == CALLBACK_CONFIRM_EXPENSE:
            pending = context.user_data.get('pending_expense')
            if not pending: await query.edit_message_text("😕 Dados do gasto expiraram. Envie novamente."); return
            savie.add_expense(user_id, pending['amount'], pending['desc'], pending['category'], date.today())
            await query.edit_message_text(f"✅ *Gasto registrado!*\n\n{pending['category']}: R$ {pending['amount']:.2f} - {pending['desc']}", parse_mode='Markdown')
            expense_data = context.user_data.pop('pending_expense', None)
            if expense_data: await check_for_anomalies_and_patterns(user_id, expense_data, context)
        elif action == CALLBACK_CONFIRM_INSTALLMENT:
            pending = context.user_data.get('pending_installment')
            if not pending: await query.edit_message_text("😕 Dados do parcelamento expiraram. Envie novamente."); return
            savie.add_installment_purchase(user_id, pending['total_amount'], pending['desc'], pending['category'], pending['count'], date.today())
            await query.edit_message_text(f"💳 *Parcelamento registrado!*\n\n🛍️ {pending['desc']} foi agendado em {pending['count']} parcelas.", parse_mode='Markdown'); del context.user_data['pending_installment']
        elif action == CALLBACK_CANCEL:
            context.user_data.clear(); await query.edit_message_text("❌ Operação cancelada.")
        elif action == CALLBACK_DELETE_MENU_LAST:
            last_expense = savie.get_last_expense(user_id)
            if not last_expense: await query.edit_message_text("Nenhum gasto encontrado para excluir."); return
            exp_id, desc, amount, cat = last_expense['id'], last_expense['description'], Decimal(last_expense['amount']), last_expense['category']
            text = f"Tem certeza que deseja excluir este gasto?\n\n*{cat}*: {desc} - R$ {amount:.2f}"; keyboard = [[InlineKeyboardButton("👍 Sim, excluir", callback_data=f"{CALLBACK_DELETE_CONFIRM_LAST}|{exp_id}"), InlineKeyboardButton("❌ Não", callback_data=CALLBACK_CANCEL)]]
            await query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))
        elif action == CALLBACK_DELETE_CONFIRM_LAST:
            expense_id_to_delete = int(payload); savie.delete_expense_by_id(expense_id_to_delete, user_id)
            await query.edit_message_text("✅ Último gasto excluído com sucesso.")
        elif action == CALLBACK_DELETE_MENU_ALL:
            text = ("⚠️ *AÇÃO IRREVERSÍVEL!*\n\nVocê tem certeza que deseja apagar *TODOS* os seus dados (gastos, parcelamentos, assinaturas e desafios)?\n\nEsta ação não pode ser desfeita.")
            keyboard = [[InlineKeyboardButton("🔥 SIM, APAGAR TUDO", callback_data=CALLBACK_DELETE_CONFIRM_ALL), InlineKeyboardButton("❌ NÃO, CANCELAR", callback_data=CALLBACK_CANCEL)]]
            await query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))
        elif action == CALLBACK_DELETE_CONFIRM_ALL:
            savie.delete_all_user_data(user_id); await query.edit_message_text("🗑️ Todos os seus dados foram apagados permanentemente.")
        elif action == CALLBACK_ADD_RECURRING:
            pending_suggestion = context.user_data.get('suggestion_for_recurring')
            if not pending_suggestion: await query.edit_message_text("😕 Os dados desta sugestão expiraram."); return
            day_of_month = date.today().day; savie.add_recurring_expense(user_id, day_of_month, pending_suggestion)
            await query.edit_message_text(f"✅ Assinatura '{pending_suggestion['desc']}' criada! Ela será lançada automaticamente todo dia {day_of_month}."); del context.user_data['suggestion_for_recurring']
        elif action == CALLBACK_CHALLENGE_ACCEPT:
            challenge_category, challenge_days = payload.split('|')
            savie.start_no_spend_challenge(user_id, challenge_category, int(challenge_days))
            await query.edit_message_text(f"💪 Desafio aceito! Boa sorte nos próximos {challenge_days} dias. Estou de olho!")
    except Exception as e:
        logger.error(f"Erro no callback '{query.data}': {e}"); await query.edit_message_text("😕 Ocorreu um erro. Tente novamente.")

async def exportar_dados(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("Desculpe, este é um comando restrito ao administrador.")
        return
    await update.message.reply_text("Gerando o relatório de usuários... por favor, aguarde.")
    try:
        cursor = savie.conn.cursor()
        cursor.execute("SELECT full_name, email, created_at FROM users WHERE full_name IS NOT NULL AND email IS NOT NULL")
        users = cursor.fetchall()
        if not users:
            await update.message.reply_text("Nenhum usuário com cadastro completo encontrado.")
            return
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['Nome Completo', 'Email', 'Data de Cadastro'])
        for user in users:
            writer.writerow([user['full_name'], user['email'], user['created_at']])
        output.seek(0)
        document = io.BytesIO(output.getvalue().encode('utf-8'))
        document.name = f"savie_usuarios_{datetime.now().strftime('%Y-%m-%d')}.csv"
        await context.bot.send_document(chat_id=ADMIN_ID, document=document)
    except Exception as e:
        logger.error(f"Erro ao exportar dados: {e}")
        await update.message.reply_text(f"Ocorreu um erro ao gerar o relatório: {e}")

async def ajuda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await gatekeeper(update, context, "ajuda"): return
    help_text = ("*🤖 Central de Ajuda do Savie*\n\n*Como Registrar Gastos*\n• *Texto:* Envie `Cinema 50 reais`.\n• *Parcelas:* Envie `TV 2500 em 10x`.\n\n*Comandos*\n`/start` - Reinicia o bot.\n`/gastos` - Resumo do mês.\n`/categorias` - Gastos por categoria.\n`/parcelas` - Compras parceladas ativas.\n`/desafio` - Comece um desafio para economizar.\n`/excluir` - Apagar registros.\n`/rachar` - (Em grupos) Dividir uma conta.\n`/ajuda` - Exibe esta mensagem.")
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def gastos_mes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await gatekeeper(update, context, "gastos"): return
    user_id = update.effective_user.id; summary = savie.get_monthly_summary(user_id)
    if not summary: await update.message.reply_text("Você ainda não registrou nenhum gasto este mês. Comece agora!"); return
    month_name = datetime.now().strftime('%B de %Y').capitalize()
    report = f"📊 *Resumo de {month_name}*\n\n💰 *Total Gasto:* R$ {summary['total']:.2f}\n\nPara ver o detalhamento, use o botão 'Por Categoria'."
    await update.message.reply_text(report, parse_mode='Markdown')

async def gastos_por_categoria(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await gatekeeper(update, context, "categorias"): return
    user_id = update.effective_user.id; summary = savie.get_monthly_summary(user_id)
    if not summary: await update.message.reply_text("Você ainda não registrou nenhum gasto este mês."); return
    month_name = datetime.now().strftime('%B de %Y').capitalize()
    report = f"📈 *Gastos por Categoria - {month_name}*\n\n"; total_geral = summary['total']
    for row in summary['by_category']:
        category, amount = row['category'], Decimal(row['cat_total']); percentage = (amount / total_geral) * 100
        report += f"{category}: *R$ {amount:.2f}* ({percentage:.1f}%)\n"
    report += f"\n💰 *Total Geral:* R$ {total_geral:.2f}"; await update.message.reply_text(report, parse_mode='Markdown')

async def compras_parceladas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await gatekeeper(update, context, "parcelas"): return
    user_id = update.effective_user.id; installments = savie.get_active_installments(user_id)
    if not installments: await update.message.reply_text("Você não possui nenhuma compra parcelada ativa no momento. ✅"); return
    report = "💳 *Suas Compras Parceladas Ativas*\n\n"; total_pending = Decimal(0)
    for item in installments:
        total_amount = Decimal(item['total_amount']); installment_amount = total_amount / item['total_installments']
        remaining_installments = item['total_installments'] - item['paid_count']; remaining_amount = remaining_installments * installment_amount
        total_pending += remaining_amount; report += f"🛍️ *{item['description']}*\n"
        report += f" ({item['paid_count']}/{item['total_installments']}) *R$ {installment_amount:.2f}* por mês\n"
        report += f"💸 Restam *R$ {remaining_amount:.2f}*\n\n"
    report += f"💰 *Total pendente de todas as parcelas: R$ {total_pending:.2f}*"; await update.message.reply_text(report, parse_mode='Markdown')

async def excluir(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await gatekeeper(update, context, "excluir"): return
    text = "Selecione o que você deseja excluir:"; keyboard = [[InlineKeyboardButton("🗑️ Excluir Último Gasto", callback_data=CALLBACK_DELETE_MENU_LAST)], [InlineKeyboardButton("🔥 APAGAR TUDO", callback_data=CALLBACK_DELETE_MENU_ALL)], [InlineKeyboardButton("❌ Cancelar", callback_data=CALLBACK_CANCEL)]]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def desafio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await gatekeeper(update, context, "desafio"): return
    challenge_category = "🍽️ Alimentação"; challenge_days = 7
    text = (f"Olá! Que tal um desafio para apimentar sua jornada financeira?\n\n"
            f"🎯 *O Desafio:* Ficar **{challenge_days} dias** sem registrar nenhum gasto na categoria *{challenge_category}* (restaurantes, iFood, etc.).\n\n"
            "Isso te ajudará a economizar e ter mais consciência dos seus gastos. Aceita?")
    payload = f"{challenge_category}|{challenge_days}"
    keyboard = [[InlineKeyboardButton("✅ Sim, aceito o desafio!", callback_data=f"{CALLBACK_CHALLENGE_ACCEPT}|{payload}"), InlineKeyboardButton("❌ Talvez depois", callback_data=CALLBACK_CANCEL)]]
    await update.message.reply_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

async def rachar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await gatekeeper(update, context, "rachar"): return
    if update.message.chat.type == 'private':
        await update.message.reply_text("Este comando só funciona em grupos!"); return
    try:
        args = context.args
        if len(args) < 2 or not args[0].replace('.','',1).replace(',','',1).isdigit() or not any(a.startswith('@') for a in args):
            await update.message.reply_text("Uso: /rachar <valor> @amigo1 @amigo2... <descrição>"); return
        total_amount = Decimal(args[0].replace(',', '.'))
        mentions = [arg.replace('@', '') for arg in args if arg.startswith('@')]
        creator_username = update.message.from_user.username
        if creator_username and creator_username not in mentions:
            mentions.append(creator_username)
        description_parts = [arg for arg in args[1:] if not arg.startswith('@')]
        description = ' '.join(description_parts) or "Conta compartilhada"
        num_participants = len(mentions)
        amount_per_person = total_amount / num_participants
        bill_id = savie.create_shared_bill(update.message.from_user.id, creator_username, update.message.chat_id, description, total_amount)
        participant_ids = {username: savie.add_bill_participant(bill_id, username, amount_per_person) for username in mentions}
        bill_info, participants = savie.get_bill_status(bill_id)
        summary_text = f"**Conta Rachada por @{creator_username}**\n\n📝 *Descrição:* {description}\n💰 *Total:* R$ {total_amount:.2f} (R$ {amount_per_person:.2f} por pessoa)\n\n*Participantes:*\n"
        for p in participants: summary_text += f"⏳ @{p['participant_username']}\n"
        summary_message = await update.message.reply_text(summary_text, parse_mode=ParseMode.MARKDOWN)
        savie.update_bill_summary_message(bill_id, summary_message.message_id)
        for username, participant_id in participant_ids.items():
            if username == creator_username: continue
            user = savie.get_user_by_username(username)
            if user:
                try:
                    dm_text = f"Olá, @{username}! O @{creator_username} te incluiu em uma conta de '{description}'.\n💸 *Sua parte:* R$ {amount_per_person:.2f}\nClique abaixo quando pagar."; keyboard = [[InlineKeyboardButton("✅ Já paguei", callback_data=f"{CALLBACK_PAY_BILL}|{participant_id}")]]
                    await context.bot.send_message(chat_id=user['user_id'], text=dm_text, reply_markup=InlineKeyboardMarkup(keyboard))
                except Exception as e: logger.error(f"Não foi possível enviar DM para {username}: {e}"); await context.bot.send_message(chat_id=update.message.chat_id, text=f"PS: Não consegui avisar @{username}. Ele(a) precisa iniciar uma conversa comigo primeiro (/start).")
    except Exception as e:
        logger.error(f"Erro no comando /rachar: {e}"); await update.message.reply_text("Ocorreu um erro ao processar o racha da conta.")

async def handle_keyboard_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "📊 Gastos do Mês": await gastos_mes(update, context)
    elif text == "📈 Por Categoria": await gastos_por_categoria(update, context)
    elif text == "💳 Ver Parcelas": await compras_parceladas(update, context)
    elif text == "🎯 Desafios": await desafio(update, context)
    elif text == "🗑️ Excluir Dados": await excluir(update, context)
    elif text == "❓ Ajuda": await ajuda(update, context)

def main() -> None:
    if not BOT_TOKEN:
        logger.error("ERRO: O BOT_TOKEN não foi definido."); return

    application = Application.builder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("ajuda", ajuda))
    application.add_handler(CommandHandler("gastos", gastos_mes))
    application.add_handler(CommandHandler("categorias", gastos_por_categoria))
    application.add_handler(CommandHandler("parcelas", compras_parceladas))
    application.add_handler(CommandHandler("excluir", excluir))
    application.add_handler(CommandHandler("desafio", desafio))
    application.add_handler(CommandHandler("rachar", rachar))
    application.add_handler(CommandHandler("exportar", exportar_dados))
    
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(handle_callback))
    
    # Lembrete: Para o JobQueue funcionar, instale com: pip install "python-telegram-bot[job-queue]"
    if application.job_queue:
        application.job_queue.run_repeating(daily_scheduler_job, interval=6*60*60, first=10)
    
    application.run_polling()

if __name__ == '__main__':
    main()