from flask import Flask, render_template, request, redirect, url_for, send_file, flash
import xml.etree.ElementTree as ET
import pandas as pd
import os
from fpdf import FPDF
from babel.numbers import format_currency
from werkzeug.utils import secure_filename
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import locale

# Configuração de localidade para formatação monetária
try:
    locale.setlocale(locale.LC_ALL, 'pt_BR.UTF-8')
except locale.Error:
    pass

app = Flask(__name__)
app.secret_key = 'sua_chave_secreta'
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Configuração do banco de dados
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///notas_fiscais.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

class NotaFiscal(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    numero = db.Column(db.String(50), nullable=False)
    data = db.Column(db.Date, nullable=False)
    codigo_servico = db.Column(db.String(50))
    valor_iss = db.Column(db.Float, default=0.00)
    valor_inss = db.Column(db.Float, default=0.00)
    valor_nota = db.Column(db.Float, default=0.00)
    situacao = db.Column(db.String(50))
    xml_filename = db.Column(db.String(255))

with app.app_context():
    db.create_all()

# Filtro personalizado para formatação monetária
@app.template_filter('format_currency_brl')
def format_currency_brl_filter(value):
    return format_currency(value, 'BRL', locale='pt_BR')

def parse_monetary(value, is_inss=False):
    try:
        if is_inss:
            return float(value.replace(',', ''))  # INSS: ponto como decimal
        else:
            return float(value.replace('.', '').replace(',', '.'))  # Demais: vírgula como decimal
    except (ValueError, AttributeError):
        return 0.00

def get_xml_value(element, path, ns):
    found = element.find(path, ns)
    return found.text if found is not None else "0"

def parse_date(date_str):
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            pass
    raise ValueError(f"Formato de data inválido: {date_str}")

def parse_xml(file_path, filename):
    tree = ET.parse(file_path)
    root = tree.getroot()
    ns = {'ns': root.tag.split('}')[0].strip('{')}

    numero = get_xml_value(root, "ns:ChaveNFe/ns:NumeroNFe", ns)
    data = parse_date(get_xml_value(root, "ns:ChaveNFe/ns:DataEmissaoNFe", ns))
    codigo_servico = get_xml_value(root, "ns:CodigoServico", ns)
    status = get_xml_value(root, "ns:StatusNFe", ns)
    tributacao = get_xml_value(root, "ns:TributacaoNFe", ns)

    if status == "Cancelada":
        valor_iss = valor_inss = valor_nota = 0.00
    else:
        valor_iss = parse_monetary(get_xml_value(root, "ns:ValorISS", ns))
        valor_inss = parse_monetary(get_xml_value(root, "ns:ValorInss", ns), is_inss=True)
        valor_nota = parse_monetary(get_xml_value(root, "ns:ValorServicos", ns))

    situacao = "Não Tributada"
    if tributacao == "Retida no Tomador":
        situacao = "Tributada no Tomador"
    elif status == "Cancelada":
        situacao = "Cancelada"

    db.session.add(NotaFiscal(
        numero=numero,
        data=data,
        codigo_servico=codigo_servico,
        valor_iss=valor_iss,
        valor_inss=valor_inss,
        valor_nota=valor_nota,
        situacao=situacao,
        xml_filename=filename
    ))
    db.session.commit()

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST' and 'file' in request.files:
        files = request.files.getlist('file')
        total_uploaded = 0
        total_failed = 0
        failed_reasons = []

        for file in files:
            if file and file.filename.endswith('.xml'):
                try:
                    filename = secure_filename(file.filename)
                    file_path = os.path.join(UPLOAD_FOLDER, filename)
                    file.save(file_path)
                    parse_xml(file_path, filename)
                    os.remove(file_path)
                    total_uploaded += 1
                except Exception as e:
                    failed_reasons.append(f"{file.filename}: {str(e)}")
                    total_failed += 1

        if total_uploaded > 0:
            flash(f'{total_uploaded} arquivo(s) carregado(s) com sucesso!', 'success')
        if total_failed > 0:
            flash(f'{total_failed} arquivo(s) falharam ao carregar: ' + ', '.join(failed_reasons), 'danger')

        return redirect(url_for('index'))

    # Bloco GET (executa após redirecionamento ou acesso direto)
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    query = NotaFiscal.query

    if start_date and end_date:
        try:
            start_date = datetime.strptime(start_date, "%Y-%m-%d")
            end_date = datetime.strptime(end_date, "%Y-%m-%d")
            query = query.filter(NotaFiscal.data.between(start_date, end_date))
        except ValueError:
            flash('Formato de data inválido. Use o formato YYYY-MM-DD.', 'danger')

    notas = query.all()
    total_notas = sum(nota.valor_nota for nota in notas)
    total_iss = sum(nota.valor_iss for nota in notas)
    total_inss = sum(nota.valor_inss for nota in notas)

    return render_template('index.html', 
                         data=notas,
                         total_notas=format_currency(total_notas, 'BRL', locale='pt_BR'),
                         total_iss=format_currency(total_iss, 'BRL', locale='pt_BR'),
                         total_inss=format_currency(total_inss, 'BRL', locale='pt_BR'))

@app.route('/clear_data', methods=['POST'])
def clear_data():
    db.session.query(NotaFiscal).delete()
    db.session.commit()
    return redirect(url_for('index'))

@app.route('/download/csv')
def download_csv():
    notas = NotaFiscal.query.all()
    
    df = pd.DataFrame([{
        "Número": nota.numero,
        "Data": nota.data.strftime("%d/%m/%Y"),
        "Código Serviço": nota.codigo_servico,
        "Valor ISS": locale.currency(nota.valor_iss, grouping=True),
        "Valor INSS": locale.currency(nota.valor_inss, grouping=True),
        "Valor da Nota": locale.currency(nota.valor_nota, grouping=True),
        "Situação": nota.situacao
    } for nota in notas])

    csv_path = os.path.join(UPLOAD_FOLDER, 'dados.csv')
    df.to_csv(csv_path, index=False, encoding='utf-8-sig', sep=';')
    
    return send_file(csv_path, as_attachment=True)

@app.route('/download/pdf')
def download_pdf():
    notas = NotaFiscal.query.all()
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=10)

    headers = ['Data', 'Número', 'Código', 'ISS', 'INSS', 'Valor', 'Situação']
    col_widths = [25, 30, 25, 25, 25, 30, 30]
    
    for header, width in zip(headers, col_widths):
        pdf.cell(width, 10, header, border=1)
    pdf.ln()

    for nota in notas:
        pdf.cell(col_widths[0], 10, nota.data.strftime("%d/%m/%Y"), border=1)
        pdf.cell(col_widths[1], 10, nota.numero, border=1)
        pdf.cell(col_widths[2], 10, nota.codigo_servico, border=1)
        pdf.cell(col_widths[3], 10, locale.currency(nota.valor_iss, grouping=True), border=1)
        pdf.cell(col_widths[4], 10, locale.currency(nota.valor_inss, grouping=True), border=1)
        pdf.cell(col_widths[5], 10, locale.currency(nota.valor_nota, grouping=True), border=1)
        pdf.cell(col_widths[6], 10, nota.situacao, border=1)
        pdf.ln()

    pdf_output_path = os.path.join(UPLOAD_FOLDER, 'relatorio.pdf')
    pdf.output(pdf_output_path)
    
    return send_file(pdf_output_path, as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True)