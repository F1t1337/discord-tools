"""
Web Dashboard API для Discord Token Manager
Предоставляет REST API для получения статистики и управления Pipeline
"""

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import logging
import os
import sys
from datetime import datetime, timedelta

# Добавляем путь к модулям
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from modules.database import Database

app = Flask(__name__, static_folder='dashboard', static_url_path='')
CORS(app)  # Разрешаем CORS для разработки

# Глобальные переменные
pipeline = None
config = None
db = None

logger = logging.getLogger(__name__)


def init_dashboard(pipeline_instance, config_dict):
    """Инициализация dashboard с pipeline и конфигом"""
    global pipeline, config, db
    pipeline = pipeline_instance
    config = config_dict
    db = Database(config['database']['path'])
    logger.info("✅ Dashboard API инициализирован")


# ==================== СТАТИЧЕСКИЕ ФАЙЛЫ ====================

@app.route('/')
def index():
    """Главная страница dashboard"""
    return send_from_directory('dashboard', 'index.html')


# ==================== API ENDPOINTS ====================

@app.route('/api/status')
def get_status():
    """Получить текущий статус Pipeline"""
    try:
        if not pipeline:
            return jsonify({'error': 'Pipeline not initialized'}), 500
        
        status = pipeline.get_status()
        counts = db.count_tokens_by_status()
        
        return jsonify({
            'running': status['running'],
            'queues': status['queues'],
            'counts': counts,
            'threads': {
                name: {
                    'alive': is_alive,
                    'status': 'running' if is_alive else 'stopped'
                }
                for name, is_alive in status['threads'].items()
            }
        })
    except Exception as e:
        logger.error(f"Error getting status: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/statistics/today')
def get_today_stats():
    """Получить статистику за сегодня"""
    try:
        stats = db.get_today_statistics()
        return jsonify(stats)
    except Exception as e:
        logger.error(f"Error getting today stats: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/statistics/range')
def get_stats_range():
    """Получить статистику за период"""
    try:
        days = request.args.get('days', 7, type=int)
        stats = db.get_statistics_range(days)
        return jsonify(stats)
    except Exception as e:
        logger.error(f"Error getting stats range: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/statistics/total')
def get_total_stats():
    """Получить общую статистику"""
    try:
        stats = db.get_total_statistics()
        return jsonify(stats)
    except Exception as e:
        logger.error(f"Error getting total stats: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/tokens')
def get_tokens():
    """Получить список токенов с фильтрацией"""
    try:
        status = request.args.get('status', None)
        limit = request.args.get('limit', 50, type=int)
        
        if status:
            tokens = db.get_tokens_by_status(status)[:limit]
        else:
            # Получаем все статусы
            all_tokens = []
            for s in ['new', 'validated', 'cleaning', 'cleaned', 'ready', 'sent', 'invalid', 'locked']:
                all_tokens.extend(db.get_tokens_by_status(s))
            tokens = sorted(all_tokens, key=lambda x: x['created_at'], reverse=True)[:limit]
        
        # Преобразуем timestamp в читаемый формат
        for token in tokens:
            if token.get('created_at'):
                token['created_at_str'] = datetime.fromtimestamp(token['created_at']).strftime('%Y-%m-%d %H:%M:%S')
        
        return jsonify(tokens)
    except Exception as e:
        logger.error(f"Error getting tokens: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/tokens/cleaning')
def get_cleaning_tokens():
    """Получить токены в процессе очистки"""
    try:
        tokens = db.get_tokens_by_status('cleaning')[:20]
        
        # Добавляем читаемые даты
        for token in tokens:
            if token.get('created_at'):
                token['created_at_str'] = datetime.fromtimestamp(token['created_at']).strftime('%Y-%m-%d %H:%M:%S')
        
        return jsonify(tokens)
    except Exception as e:
        logger.error(f"Error getting cleaning tokens: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/logs')
def get_logs():
    """Получить последние логи"""
    try:
        limit = request.args.get('limit', 100, type=int)
        level = request.args.get('level', None)
        
        logs = db.get_recent_logs(limit, level)
        
        # Преобразуем timestamp в читаемый формат
        for log in logs:
            if log.get('timestamp'):
                log['timestamp_str'] = datetime.fromtimestamp(log['timestamp']).strftime('%Y-%m-%d %H:%M:%S')
        
        return jsonify(logs)
    except Exception as e:
        logger.error(f"Error getting logs: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/balance')
def get_balance():
    """Получить баланс LZT"""
    try:
        if not pipeline or not pipeline.lzt_monitor:
            return jsonify({'error': 'LZT Monitor not initialized'}), 500
        
        balance = pipeline.lzt_monitor.get_balance()
        
        return jsonify({
            'balance': balance,
            'min_balance': config['lzt']['min_balance_alert'],
            'is_low': balance < config['lzt']['min_balance_alert'] if balance else False
        })
    except Exception as e:
        logger.error(f"Error getting balance: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/config')
def get_config():
    """Получить текущую конфигурацию (без секретов)"""
    try:
        safe_config = {
            'lzt': {
                'check_interval': config['lzt']['check_interval'],
                'min_balance_alert': config['lzt']['min_balance_alert']
            },
            'validator': config['validator'],
            'cleaner': config['cleaner'],
            'telegram': {
                'min_tokens': config['telegram']['min_tokens'],
                'max_tokens': config['telegram']['max_tokens']
            },
            'proxy': config['proxy']
        }
        return jsonify(safe_config)
    except Exception as e:
        logger.error(f"Error getting config: {e}")
        return jsonify({'error': str(e)}), 500


# ==================== УПРАВЛЕНИЕ PIPELINE ====================

@app.route('/api/control/pause', methods=['POST'])
def pause_pipeline():
    """Приостановить Pipeline"""
    try:
        # TODO: Добавить логику паузы
        return jsonify({'success': True, 'message': 'Pipeline paused'})
    except Exception as e:
        logger.error(f"Error pausing pipeline: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/control/resume', methods=['POST'])
def resume_pipeline():
    """Возобновить Pipeline"""
    try:
        # TODO: Добавить логику возобновления
        return jsonify({'success': True, 'message': 'Pipeline resumed'})
    except Exception as e:
        logger.error(f"Error resuming pipeline: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/control/stop', methods=['POST'])
def stop_pipeline():
    """Остановить Pipeline"""
    try:
        if pipeline:
            pipeline.stop()
            return jsonify({'success': True, 'message': 'Pipeline stopped'})
        return jsonify({'error': 'Pipeline not running'}), 400
    except Exception as e:
        logger.error(f"Error stopping pipeline: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/tokens/reset_cleaning', methods=['POST'])
def reset_token_cleaning():
    """Сбросить очистку токена"""
    try:
        data = request.get_json()
        token = data.get('token')
        
        if not token:
            return jsonify({'error': 'Token not provided'}), 400
        
        if not pipeline:
            return jsonify({'error': 'Pipeline not initialized'}), 500
        
        # Вызываем метод сброса
        success = pipeline.reset_token_cleaning(token)
        
        if success:
            return jsonify({
                'success': True,
                'message': 'Токен возвращен в очередь очистки'
            })
        else:
            return jsonify({
                'success': False,
                'message': 'Не удалось сбросить очистку'
            }), 400
        
    except Exception as e:
        logger.error(f"Error resetting cleaning: {e}")
        return jsonify({'error': str(e)}), 500


# ==================== HEALTH CHECK ====================

@app.route('/api/health')
def health_check():
    """Проверка работоспособности API"""
    return jsonify({
        'status': 'ok',
        'timestamp': datetime.now().isoformat(),
        'pipeline_initialized': pipeline is not None,
        'database_connected': db is not None
    })


# ==================== CLOUDFLARE TUNNEL URL ====================

@app.route('/api/tunnel-url')
@app.route('/api/ngrok-url')  # Оставляем для обратной совместимости
def get_tunnel_url():
    """Получить текущий Cloudflare Tunnel URL"""
    try:
        if not pipeline:
            return jsonify({'error': 'Pipeline not initialized'}), 503
        
        # Получаем URL от Cloudflare helper
        tunnel_url = pipeline.cloudflare.get_public_url()
        
        if tunnel_url:
            return jsonify({
                'url': tunnel_url,
                'status': 'active',
                'type': 'cloudflare',
                'timestamp': datetime.now().isoformat()
            })
        else:
            return jsonify({
                'url': None,
                'status': 'inactive',
                'message': 'Cloudflare Tunnel не запущен',
                'timestamp': datetime.now().isoformat()
            }), 200
            
    except Exception as e:
        logger.error(f"Error getting tunnel URL: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/miniapp')
def miniapp():
    """Отдает Mini App HTML"""
    return send_from_directory('dashboard', 'miniapp.html')


def run_dashboard(host='0.0.0.0', port=5000, debug=False):
    """Запустить dashboard сервер"""
    logger.info(f"🌐 Запуск Web Dashboard на http://{host}:{port}")
    app.run(host=host, port=port, debug=debug, use_reloader=False)


if __name__ == "__main__":
    # Тестовый запуск
    logging.basicConfig(level=logging.INFO)
    run_dashboard(debug=True)