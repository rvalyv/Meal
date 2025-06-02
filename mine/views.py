# mine/views.py
import os
import re

from django.core.paginator import Paginator
from django.shortcuts import render, redirect

from simple import settings
from .logging_utils import log_user_activity
from .models import Meal, Ingredient, MealIngredient, Delivery, MealServe
from .forms import MealForm, IngredientForm, DeliveryForm
from django.shortcuts import render, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.db.models import Q
from django.utils import timezone
from django.db.models import Sum, Max, F, Count
from datetime import datetime, timedelta
import logging
import json
from django.db import models


from mine.utils import send_stock_update


serve_logger = logging.getLogger('meal_serve')
delivery_logger = logging.getLogger('delivery')

def meal_list(request):
    meals = Meal.objects.all()
    return render(request, 'meal_list.html', {'meals': meals})

def create_meal(request):
    ingredients = Ingredient.objects.all()
    if request.method == 'POST':
        form = MealForm(request.POST)
        if form.is_valid():
            meal = form.save()
            
            # Get lists of ingredient ids and quantities from POST data
            ingredient_ids = request.POST.getlist('ingredients')
            quantities = request.POST.getlist('quantities')
            
            for ing_id, qty in zip(ingredient_ids, quantities):
                if ing_id and qty:
                    MealIngredient.objects.create(
                        meal=meal,
                        ingredient_id=int(ing_id),
                        quantity=float(qty)
                    )
            
            return redirect('meal_list')
    else:
        form = MealForm()

    return render(request, 'create_meal.html', {
        'form': form,
        'ingredients': ingredients
    })


def delete_meal(request, meal_id):
    meal = get_object_or_404(Meal, id=meal_id)
    
    if request.method == 'POST':
        meal.delete()
        return redirect('meal_list')
    
    # Optional: Confirm delete page
    return render(request, 'confirm_delete_meal.html', {
        'meal': meal
    })

def update_meal(request, meal_id):
    meal = get_object_or_404(Meal, id=meal_id)
    ingredients = Ingredient.objects.all()
    
    if request.method == 'POST':
        form = MealForm(request.POST, instance=meal)
        if form.is_valid():
            meal = form.save()
            
            # Clear existing related MealIngredients
            MealIngredient.objects.filter(meal=meal).delete()
            
            # Update with new ingredient data
            ingredient_ids = request.POST.getlist('ingredients')
            quantities = request.POST.getlist('quantities')
            
            for ing_id, qty in zip(ingredient_ids, quantities):
                if ing_id and qty:
                    MealIngredient.objects.create(
                        meal=meal,
                        ingredient_id=int(ing_id),
                        quantity=float(qty)
                    )
            
            return redirect('meal_list')
    else:
        form = MealForm(instance=meal)
        
        # Prepare existing ingredients and quantities for the form template
        existing_ingredients = MealIngredient.objects.filter(meal=meal)
    
    return render(request, 'update_meal.html', {
        'form': form,
        'ingredients': ingredients,
        'meal': meal,
        'existing_ingredients': existing_ingredients
    })


@login_required
def ingredient_list(request):
    query = request.GET.get('q', '').strip()
    ingredients = Ingredient.objects.all()

    if query:
        ingredients = ingredients.filter(Q(name__icontains=query))

    return render(request, 'ingredient_list.html', {
        'ingredients': ingredients,
        'query': query,
    })

@login_required
def create_ingredient(request):
    if request.method == 'POST':
        form = IngredientForm(request.POST)
        if form.is_valid():
            name = form.cleaned_data['name'].strip()
            stock = form.cleaned_data.get('stock_quantity')

            if Ingredient.objects.filter(name__iexact=name).exists():
                messages.error(request, f"Ingredient '{name}' already exists.")
            elif stock <= 0:
                messages.error(request, "Stock quantity must be greater than zero.")
            else:
                ingredient = form.save(commit=False)
                ingredient.created_by = request.user  # track who created it
                ingredient.save()
                messages.success(request, f"Ingredient '{name}' created successfully!")
                return redirect('ingredient_list')
    else:
        form = IngredientForm()

    return render(request, 'create_ingredient.html', {'form': form})

@login_required
def check_ingredient_name(request):
    name = request.GET.get('name', '').strip()
    exists = Ingredient.objects.filter(name__iexact=name).exists()
    return JsonResponse({'exists': exists})


@login_required
def create_delivery(request):
    ingredients = Ingredient.objects.all()

    if request.method == 'POST':
        total_forms = int(request.POST.get('form-TOTAL_FORMS', 0))

        for i in range(total_forms):
            ing_id = request.POST.get(f'ingredient_{i}')
            qty = request.POST.get(f'quantity_{i}')

            if ing_id and qty:
                try:
                    ingredient = Ingredient.objects.get(id=ing_id)
                    qty_float = float(qty)

                    # ✅ This saves delivery and updates stock (via model logic)
                    Delivery.objects.create(
                        ingredient=ingredient,
                        quantity=qty_float,
                        delivered_by=request.user
                    )

                    # ✅ Log it
                    delivery_logger.info(f"{request.user.username} delivered {qty_float}g of {ingredient.name}")

                except (Ingredient.DoesNotExist, ValueError):
                    continue

        return redirect('ingredient_list')

    return render(request, 'create_delivery.html', {'ingredients': ingredients})


@login_required
def delivery_list_view(request):
    deliveries = Delivery.objects.select_related('ingredient', 'delivered_by').order_by('-delivered_at')
    return render(request, 'delivery_list.html', {'deliveries': deliveries})

@login_required
def update_ingredient(request, ingredient_id):
    ingredient = get_object_or_404(Ingredient, id=ingredient_id)

    if request.method == 'POST':
        form = IngredientForm(request.POST, instance=ingredient)
        if form.is_valid():
            form.save()
            return redirect('ingredient_list')
    else:
        form = IngredientForm(instance=ingredient)

    return render(request, 'update_ingredient.html', {
        'form': form,
        'ingredient': ingredient
    })

def delete_ingredient(request, ingredient_id):
    ingredient = get_object_or_404(Ingredient, id=ingredient_id)
    
    if request.method == 'POST':
        ingredient.delete()
        return redirect('ingredient_list')
    
    # Optional: Confirm delete page
    return render(request, 'confirm_delete_ingredient.html', {
        'ingredient': ingredient
    })


@login_required
@log_user_activity("dashboard")
def dashboard_view(request):
    """Enhanced dashboard view with real data from your models"""

    # Get current date and month boundaries
    now = timezone.now()
    current_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # Calculate statistics
    context = {}

    # 1. Meals Served This Month (count of MealServe records)
    meals_served = MealServe.objects.filter(
        date__gte=current_month_start
    ).count()

    # 2. Total Delivered This Month (sum of all delivery quantities)
    total_delivered = Delivery.objects.filter(
        delivered_at__gte=current_month_start
    ).aggregate(
        total_quantity=Sum('quantity')
    )['total_quantity'] or 0

    # 3. Total Ingredients Count
    ingredients_count = Ingredient.objects.count()

    # 4. Top Stock Item (ingredient with highest stock)
    max_ingredient = Ingredient.objects.filter(
        stock_quantity__gt=0
    ).order_by('-stock_quantity').first()

    # 5. Generate Meal Chart Data (last 14 days)
    meal_chart_data = []
    for i in range(13, -1, -1):  # Last 14 days
        date = now.date() - timedelta(days=i)
        daily_meals = MealServe.objects.filter(
            date__date=date
        ).count()

        meal_chart_data.append({
            'date': date.isoformat(),
            'count': daily_meals
        })

    # 6. Generate Delivery Chart Data (ingredient distribution this month)
    delivery_chart_data = []
    ingredient_deliveries = Delivery.objects.filter(
        delivered_at__gte=current_month_start
    ).values('ingredient__name').annotate(
        total_delivered=Sum('quantity')
    ).order_by('-total_delivered')[:8]  # Top 8 ingredients

    for delivery in ingredient_deliveries:
        delivery_chart_data.append({
            'name': delivery['ingredient__name'],
            'value': float(delivery['total_delivered'])
        })

    # 7. Recent Activity (last 10 meal serves and deliveries)
    recent_serves = MealServe.objects.select_related('meal', 'served_by').order_by('-date')[:5]
    recent_deliveries = Delivery.objects.select_related('ingredient', 'delivered_by').order_by('-delivered_at')[:5]

    # Combine and sort recent activities
    recent_activities = []

    for serve in recent_serves:
        recent_activities.append({
            'type': 'meal',
            'icon': 'utensils',
            'text': f"{serve.meal.name} served by {serve.served_by.username if serve.served_by else 'Unknown'}",
            'time': serve.date,
            'time_display': get_time_display(serve.date)
        })

    for delivery in recent_deliveries:
        recent_activities.append({
            'type': 'delivery',
            'icon': 'truck',
            'text': f"{delivery.ingredient.name} delivered - {delivery.quantity}g by {delivery.delivered_by.username if delivery.delivered_by else 'Unknown'}",
            'time': delivery.delivered_at,
            'time_display': get_time_display(delivery.delivered_at)
        })

    # Sort by time (most recent first)
    recent_activities.sort(key=lambda x: x['time'], reverse=True)
    recent_activities = recent_activities[:10]  # Keep only top 10

    # 8. Low Stock Alerts (ingredients below their threshold)
    low_stock_ingredients = Ingredient.objects.filter(
        stock_quantity__lt=models.F('treshold_quantity')
    ).order_by('stock_quantity')[:5]

    # 9. Popular Meals (most served this month)
    popular_meals = MealServe.objects.filter(
        date__gte=current_month_start
    ).values('meal__name').annotate(
        total_served=Count('id')
    ).order_by('-total_served')[:5]

    # 10. Meals that can be prepared (with available stock)
    available_meals = []
    for meal in Meal.objects.all():
        max_portions = meal.get_max_portions()
        if max_portions > 0:
            available_meals.append({
                'name': meal.name,
                'max_portions': max_portions
            })

    # Sort by max portions descending
    available_meals.sort(key=lambda x: x['max_portions'], reverse=True)
    available_meals = available_meals[:5]  # Top 5

    # 11. Ingredients running low (below threshold but not zero)
    critical_stock = Ingredient.objects.filter(
        stock_quantity__gt=0,
        stock_quantity__lt=models.F('treshold_quantity')
    ).count()

    # Prepare context
    context.update({
        'meals_served': meals_served,
        'total_delivered': total_delivered,
        'ingredients_count': ingredients_count,
        'max_ingredient': max_ingredient,
        'meal_chart': json.dumps(meal_chart_data),
        'delivery_chart': json.dumps(delivery_chart_data),
        'recent_activities': recent_activities,
        'low_stock_ingredients': low_stock_ingredients,
        'popular_meals': popular_meals,
        'available_meals': available_meals,
        'critical_stock': critical_stock,
    })

    return render(request, 'dashboard.html', context)


def get_time_display(timestamp):
    """Convert timestamp to human-readable format"""
    now = timezone.now()

    if isinstance(timestamp, datetime):
        diff = now - timestamp
    else:
        # Handle date objects
        timestamp_dt = datetime.combine(timestamp, datetime.min.time())
        timestamp_dt = timezone.make_aware(timestamp_dt)
        diff = now - timestamp_dt

    if diff.days > 0:
        if diff.days == 1:
            return "1 day ago"
        elif diff.days < 7:
            return f"{diff.days} days ago"
        elif diff.days < 30:
            weeks = diff.days // 7
            return f"{weeks} week{'s' if weeks > 1 else ''} ago"
        else:
            months = diff.days // 30
            return f"{months} month{'s' if months > 1 else ''} ago"
    else:
        hours = diff.seconds // 3600
        if hours > 0:
            return f"{hours} hour{'s' if hours > 1 else ''} ago"
        else:
            minutes = diff.seconds // 60
            if minutes > 0:
                return f"{minutes} minute{'s' if minutes > 1 else ''} ago"
            else:
                return "Just now"


@login_required
def dashboard_api(request):
    """API endpoint for real-time dashboard updates"""

    if request.method == 'GET':
        data_type = request.GET.get('type', 'all')

        now = timezone.now()
        current_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        response_data = {}

        if data_type in ['all', 'stats']:
            # Get updated statistics
            meals_served = MealServe.objects.filter(
                date__gte=current_month_start
            ).count()

            total_delivered = Delivery.objects.filter(
                delivered_at__gte=current_month_start
            ).aggregate(total_quantity=Sum('quantity'))['total_quantity'] or 0

            ingredients_count = Ingredient.objects.count()

            max_ingredient = Ingredient.objects.filter(
                stock_quantity__gt=0
            ).order_by('-stock_quantity').first()

            response_data['stats'] = {
                'meals_served': meals_served,
                'total_delivered': float(total_delivered),
                'ingredients_count': ingredients_count,
                'max_ingredient': {
                    'name': max_ingredient.name if max_ingredient else 'None',
                    'stock_quantity': float(max_ingredient.stock_quantity) if max_ingredient else 0
                }
            }

        if data_type in ['all', 'charts']:
            # Get updated chart data
            meal_chart_data = []
            for i in range(13, -1, -1):
                date = now.date() - timedelta(days=i)
                daily_meals = MealServe.objects.filter(
                    date__date=date
                ).count()

                meal_chart_data.append({
                    'date': date.isoformat(),
                    'count': daily_meals
                })

            delivery_chart_data = []
            ingredient_deliveries = Delivery.objects.filter(
                delivered_at__gte=current_month_start
            ).values('ingredient__name').annotate(
                total_delivered=Sum('quantity')
            ).order_by('-total_delivered')[:8]

            for delivery in ingredient_deliveries:
                delivery_chart_data.append({
                    'name': delivery['ingredient__name'],
                    'value': float(delivery['total_delivered'])
                })

            response_data['charts'] = {
                'meal_chart': meal_chart_data,
                'delivery_chart': delivery_chart_data
            }

        return JsonResponse(response_data)

    return JsonResponse({'error': 'Method not allowed'}, status=405)


@login_required
def get_stock_alerts(request):
    """Get low stock alerts for dashboard"""
    from django.db import models

    low_stock = Ingredient.objects.filter(
        stock_quantity__lt=models.F('treshold_quantity'),
        stock_quantity__gt=0
    ).order_by('stock_quantity')

    alerts = []
    for ingredient in low_stock:
        percentage = (ingredient.stock_quantity / ingredient.treshold_quantity) * 100
        alerts.append({
            'name': ingredient.name,
            'current_stock': float(ingredient.stock_quantity),
            'threshold': ingredient.treshold_quantity,
            'percentage': round(percentage, 1),
            'status': 'critical' if percentage < 25 else 'warning'
        })

    return JsonResponse({'alerts': alerts})


@login_required
def get_meal_availability(request):
    """Get meals that can be prepared with current stock"""
    available_meals = []

    for meal in Meal.objects.all():
        max_portions = meal.get_max_portions()
        ingredients_needed = meal.mealingredient_set.count()

        available_meals.append({
            'name': meal.name,
            'max_portions': max_portions,
            'ingredients_count': ingredients_needed,
            'can_prepare': max_portions > 0
        })

    # Sort by max portions descending
    available_meals.sort(key=lambda x: x['max_portions'], reverse=True)

    return JsonResponse({'meals': available_meals})


@login_required
def get_ingredient_usage(request):
    """Get ingredient usage statistics"""
    days = int(request.GET.get('days', 30))
    start_date = timezone.now() - timedelta(days=days)

    # Get ingredients used in served meals
    served_meals = MealServe.objects.filter(date__gte=start_date).select_related('meal')

    ingredient_usage = {}
    for serve in served_meals:
        for meal_ingredient in serve.meal.mealingredient_set.all():
            ingredient_name = meal_ingredient.ingredient.name
            quantity_used = meal_ingredient.quantity

            if ingredient_name in ingredient_usage:
                ingredient_usage[ingredient_name] += quantity_used
            else:
                ingredient_usage[ingredient_name] = quantity_used

    # Convert to list and sort
    usage_list = [
        {'name': name, 'total_used': usage}
        for name, usage in ingredient_usage.items()
    ]
    usage_list.sort(key=lambda x: x['total_used'], reverse=True)

    return JsonResponse({'usage': usage_list[:10]})  # Top 10 most used


def serve_meal(request, meal_id):
    meal = get_object_or_404(Meal, id=meal_id)
    meal_ingredients = MealIngredient.objects.filter(meal=meal)

    # Calculate maximum possible portions
    portions = 0
    if meal_ingredients.exists():
        portions_list = []
        for mi in meal_ingredients:
            if mi.quantity > 0:
                portions_count = mi.ingredient.stock_quantity / mi.quantity
                portions_list.append(portions_count)
        portions = int(min(portions_list)) if portions_list else 0

    if request.method == "POST":
        try:
            serve_quantity = int(request.POST.get("serve_quantity"))
        except (TypeError, ValueError):
            messages.error(request, "Please enter a valid number")
            return redirect('serve_meal', meal_id=meal_id)

        if serve_quantity <= 0:
            messages.error(request, "Quantity must be greater than zero")
            return redirect('serve_meal', meal_id=meal_id)

        if serve_quantity > portions:
            messages.error(request, f"Not enough stock to serve {serve_quantity} portions.")
            return redirect('serve_meal', meal_id=meal_id)

        # Reduce ingredient quantities based on serve_quantity
        for mi in meal_ingredients:
            mi.ingredient.stock_quantity -= mi.quantity * serve_quantity
            mi.ingredient.save()
            send_stock_update(mi.ingredient)  # Notify WebSocket of change

        # Create MealServe record to track this serving
        try:
            # Try to create a MealServe record if the model exists
            MealServe.objects.create(
                meal=meal,
                quantity=serve_quantity,
                date=timezone.now(),
                served_by=request.user
            )
        except Exception as e:
            # If MealServe model doesn't exist, just log the error
            serve_logger.error(f"Failed to create MealServe record: {str(e)}")

        # Log and success message
        serve_logger.info(f"{request.user.username} served {serve_quantity} portion(s) of {meal.name}")
        messages.success(request, f"Successfully served {serve_quantity} portions of {meal.name}!")
        return redirect('meal_list')

    return render(request, 'serve_meal.html', {
        'meal': meal,
        'portions': portions,
    })


@login_required
@log_user_activity("log viewer")
def log_viewer(request):
    """View system logs with filtering and pagination"""

    # Check if user has permission to view logs
    if request.user.profile.role not in ['admin', 'manager']:
        from .logging_utils import ActivityLogger
        ActivityLogger.log_permission_denied(request.user, 'view_logs', 'log_viewer')
        return render(request, 'users/permission_denied.html')

    # Get filter parameters
    log_type = request.GET.get('type', 'all')
    log_level = request.GET.get('level', 'all')
    date_range = request.GET.get('date', 'today')

    # Define log files
    log_files = {
        'meal_serve': 'meal_serve.log',
        'delivery': 'delivery.log',
        'ingredient': 'ingredient.log',
        'user_activity': 'user_activity.log',
        'security': 'security.log',
        'django': 'django.log'
    }

    # Get logs directory
    logs_dir = os.path.join(settings.BASE_DIR, 'logs')

    # Read and parse log files
    all_logs = []

    # Determine which files to read based on filter
    files_to_read = [log_files[log_type]] if log_type != 'all' else log_files.values()

    for filename in files_to_read:
        file_path = os.path.join(logs_dir, filename)
        if os.path.exists(file_path):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    lines = f.readlines()

                for line in lines:
                    parsed_log = parse_log_line(line.strip(), filename)
                    if parsed_log and should_include_log(parsed_log, log_level, date_range):
                        all_logs.append(parsed_log)
            except Exception as e:
                # Handle file reading errors
                continue

    # Sort logs by timestamp (newest first)
    all_logs.sort(key=lambda x: x['timestamp'], reverse=True)

    # Paginate results
    paginator = Paginator(all_logs, 50)  # Show 50 logs per page
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # Get log statistics
    stats = get_log_statistics(all_logs)

    context = {
        'logs': page_obj,
        'log_type': log_type,
        'log_level': log_level,
        'date_range': date_range,
        'stats': stats,
        'total_logs': len(all_logs)
    }

    return render(request, 'log_viewer.html', context)


def parse_log_line(line, filename):
    """Parse a log line into structured data"""
    if not line.strip():
        return None

    # Pattern to match log format: [timestamp] level module - message
    pattern = r'\[([^\]]+)\]\s+(\w+)\s+([^-]+)\s+-\s+(.+)'
    match = re.match(pattern, line)

    if match:
        timestamp_str, level, module, message = match.groups()

        try:
            # Parse timestamp
            timestamp = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S')
        except ValueError:
            # If timestamp parsing fails, use current time
            timestamp = datetime.now()

        return {
            'timestamp': timestamp,
            'level': level.lower(),
            'module': module.strip(),
            'message': message.strip(),
            'source': filename.replace('.log', ''),
            'raw_line': line
        }

    return None


def should_include_log(log_entry, level_filter, date_filter):
    """Determine if a log entry should be included based on filters"""

    # Level filter
    if level_filter != 'all' and log_entry['level'] != level_filter:
        return False

    # Date filter
    now = datetime.now()
    if date_filter == 'today':
        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif date_filter == 'week':
        start_date = now - timedelta(days=7)
    elif date_filter == 'month':
        start_date = now - timedelta(days=30)
    else:  # 'all'
        start_date = datetime.min

    return log_entry['timestamp'] >= start_date


def get_log_statistics(logs):
    """Calculate statistics from log entries"""
    stats = {
        'total': len(logs),
        'info': 0,
        'warning': 0,
        'error': 0,
        'by_source': {},
        'recent_activity': []
    }

    for log in logs:
        # Count by level
        level = log['level']
        if level in stats:
            stats[level] += 1

        # Count by source
        source = log['source']
        stats['by_source'][source] = stats['by_source'].get(source, 0) + 1

        # Collect recent activity (last 10 entries)
        if len(stats['recent_activity']) < 10:
            stats['recent_activity'].append(log)

    return stats


@login_required
def log_export(request):
    """Export logs as JSON or CSV"""

    # Check permissions
    if request.user.profile.role not in ['admin', 'manager']:
        return JsonResponse({'error': 'Permission denied'}, status=403)

    # Get filter parameters
    log_type = request.GET.get('type', 'all')
    log_level = request.GET.get('level', 'all')
    date_range = request.GET.get('date', 'today')
    export_format = request.GET.get('format', 'json')

    # This would implement the actual export logic
    # For now, return a simple response
    return JsonResponse({
        'message': 'Export functionality would be implemented here',
        'filters': {
            'type': log_type,
            'level': log_level,
            'date': date_range,
            'format': export_format
        }
    })