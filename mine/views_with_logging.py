from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login
from django.http import JsonResponse
from django.db.models import Q, Sum, Count
from django.utils import timezone
from django.db.models.functions import TruncDate
from datetime import datetime, timedelta
import json
import logging

from .models import Meal, Ingredient, MealIngredient, Delivery
from .forms import MealForm, IngredientForm, DeliveryForm
from .logging_utils import ActivityLogger, log_user_activity, get_client_ip
from mine.utils import send_stock_update

# Standard loggers
serve_logger = logging.getLogger('meal_serve')
delivery_logger = logging.getLogger('delivery')
ingredient_logger = logging.getLogger('ingredient')
user_activity_logger = logging.getLogger('user_activity')


@login_required
@log_user_activity("dashboard view")
def dashboard_view(request):
    """Dashboard with comprehensive logging"""
    user_activity_logger.info(f"DASHBOARD_ACCESS - User: {request.user.username}")

    today = timezone.now()
    start_of_month = datetime(today.year, today.month, 1)

    # Get statistics
    ingredients_count = Ingredient.objects.count()
    max_ingredient = Ingredient.objects.order_by('-stock_quantity').first()
    total_delivered = Delivery.objects.filter(delivered_at__gte=start_of_month).aggregate(Sum('quantity'))[
                          'quantity__sum'] or 0

    # Delivery data for charts
    delivery_data = (
        Delivery.objects
        .filter(delivered_at__gte=start_of_month)
        .values('ingredient__name')
        .annotate(total=Sum('quantity'))
        .order_by('-total')
    )

    delivery_chart_data = [
        {"name": item['ingredient__name'], "value": float(item['total'])}
        for item in delivery_data
    ]

    # Mock meal data (since MealServe might not exist)
    meals_served = 0
    meal_chart_data = []
    for i in range(7):
        date = (today - timedelta(days=i)).strftime('%Y-%m-%d')
        meal_chart_data.append({"date": date, "count": 0})

    return render(request, 'dashboard.html', {
        'meal_chart': json.dumps(meal_chart_data),
        'delivery_chart': json.dumps(delivery_chart_data),
        'meals_served': meals_served,
        'total_delivered': total_delivered,
        'max_ingredient': max_ingredient,
        'ingredients_count': ingredients_count,
    })


@login_required
@log_user_activity("meal list view")
def meal_list(request):
    """Meal list with logging"""
    meals = Meal.objects.all()
    user_activity_logger.info(f"MEAL_LIST_VIEW - User: {request.user.username} | Total meals: {meals.count()}")
    return render(request, 'meal_list.html', {'meals': meals})


@login_required
@log_user_activity("create meal")
def create_meal(request):
    """Create meal with comprehensive logging"""
    ingredients = Ingredient.objects.all()

    if request.method == 'POST':
        form = MealForm(request.POST)
        if form.is_valid():
            meal = form.save()

            # Get ingredient data
            ingredient_ids = request.POST.getlist('ingredients')
            quantities = request.POST.getlist('quantities')
            ingredients_count = 0

            for ing_id, qty in zip(ingredient_ids, quantities):
                if ing_id and qty:
                    MealIngredient.objects.create(
                        meal=meal,
                        ingredient_id=int(ing_id),
                        quantity=float(qty)
                    )
                    ingredients_count += 1

            # Log the meal creation
            ActivityLogger.log_meal_action(request.user, 'CREATE', meal, ingredients_count)
            messages.success(request, f"Meal '{meal.name}' created successfully!")
            return redirect('meal_list')
        else:
            user_activity_logger.warning(
                f"MEAL_CREATE_FAILED - User: {request.user.username} | Form errors: {form.errors}")
    else:
        form = MealForm()

    return render(request, 'create_meal.html', {
        'form': form,
        'ingredients': ingredients
    })


@login_required
@log_user_activity("serve meal")
def serve_meal(request, meal_id):
    """Serve meal with comprehensive logging"""
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

    user_activity_logger.info(
        f"SERVE_MEAL_ACCESS - User: {request.user.username} | Meal: {meal.name} | Available portions: {portions}")

    if request.method == "POST":
        try:
            serve_quantity = int(request.POST.get("serve_quantity"))
        except (TypeError, ValueError):
            ActivityLogger.log_meal_serve(request.user, meal, 0, success=False, error_msg="Invalid quantity format")
            messages.error(request, "Please enter a valid number")
            return redirect('serve_meal', meal_id=meal_id)

        if serve_quantity <= 0:
            ActivityLogger.log_meal_serve(request.user, meal, serve_quantity, success=False,
                                          error_msg="Quantity must be greater than zero")
            messages.error(request, "Quantity must be greater than zero")
            return redirect('serve_meal', meal_id=meal_id)

        if serve_quantity > portions:
            ActivityLogger.log_meal_serve(request.user, meal, serve_quantity, success=False,
                                          error_msg=f"Insufficient stock (available: {portions})")
            messages.error(request, f"Not enough stock to serve {serve_quantity} portions.")
            return redirect('serve_meal', meal_id=meal_id)

        # Log ingredient changes before serving
        ingredient_changes = []
        for mi in meal_ingredients:
            old_stock = mi.ingredient.stock_quantity
            new_stock = old_stock - (mi.quantity * serve_quantity)
            ingredient_changes.append({
                'ingredient': mi.ingredient.name,
                'old_stock': old_stock,
                'new_stock': new_stock,
                'used': mi.quantity * serve_quantity
            })

        # Reduce ingredient quantities
        for mi in meal_ingredients:
            mi.ingredient.stock_quantity -= mi.quantity * serve_quantity
            mi.ingredient.save()
            send_stock_update(mi.ingredient)

            # Check for low stock
            if mi.ingredient.stock_quantity < 100:  # Threshold of 100g
                ActivityLogger.log_stock_alert(mi.ingredient, "LOW")

        # Log successful meal serving
        ActivityLogger.log_meal_serve(request.user, meal, serve_quantity, success=True)

        # Log detailed ingredient usage
        for change in ingredient_changes:
            ingredient_logger.info(
                f"INGREDIENT_USED - Meal: {meal.name} | Ingredient: {change['ingredient']} | "
                f"Used: {change['used']}g | Old Stock: {change['old_stock']}g | "
                f"New Stock: {change['new_stock']}g"
            )

        messages.success(request, f"Successfully served {serve_quantity} portions of {meal.name}!")
        return redirect('meal_list')

    return render(request, 'serve_meal.html', {
        'meal': meal,
        'portions': portions,
    })


@login_required
@log_user_activity("create delivery")
def create_delivery(request):
    """Create delivery with comprehensive logging"""
    ingredients = Ingredient.objects.all()

    if request.method == 'POST':
        total_forms = int(request.POST.get('form-TOTAL_FORMS', 0))
        deliveries_created = 0

        for i in range(total_forms):
            ing_id = request.POST.get(f'ingredient_{i}')
            qty = request.POST.get(f'quantity_{i}')

            if ing_id and qty:
                try:
                    ingredient = Ingredient.objects.get(id=ing_id)
                    old_stock = ingredient.stock_quantity
                    qty_float = float(qty)

                    # Create delivery and update stock
                    delivery = Delivery.objects.create(
                        ingredient=ingredient,
                        quantity=qty_float,
                        delivered_by=request.user
                    )

                    # Log the delivery
                    ActivityLogger.log_delivery(request.user, ingredient, qty_float, success=True)
                    deliveries_created += 1

                except (Ingredient.DoesNotExist, ValueError) as e:
                    delivery_logger.error(f"DELIVERY_ERROR - User: {request.user.username} | Error: {str(e)}")
                    continue

        user_activity_logger.info(
            f"DELIVERY_SESSION_COMPLETE - User: {request.user.username} | Deliveries created: {deliveries_created}")
        messages.success(request, f"Successfully recorded {deliveries_created} deliveries!")
        return redirect('ingredient_list')

    return render(request, 'create_delivery.html', {'ingredients': ingredients})


@login_required
@log_user_activity("create ingredient")
def create_ingredient(request):
    """Create ingredient with comprehensive logging"""
    if request.method == 'POST':
        form = IngredientForm(request.POST)
        if form.is_valid():
            name = form.cleaned_data['name'].strip()
            stock = form.cleaned_data.get('stock_quantity')

            if Ingredient.objects.filter(name__iexact=name).exists():
                user_activity_logger.warning(
                    f"INGREDIENT_CREATE_FAILED - User: {request.user.username} | Reason: Duplicate name '{name}'")
                messages.error(request, f"Ingredient '{name}' already exists.")
            elif stock <= 0:
                user_activity_logger.warning(
                    f"INGREDIENT_CREATE_FAILED - User: {request.user.username} | Reason: Invalid stock quantity {stock}")
                messages.error(request, "Stock quantity must be greater than zero.")
            else:
                ingredient = form.save(commit=False)
                ingredient.created_by = request.user
                ingredient.save()

                # Log ingredient creation
                ActivityLogger.log_ingredient_action(request.user, 'CREATE', ingredient)
                messages.success(request, f"Ingredient '{name}' created successfully!")
                return redirect('ingredient_list')
        else:
            user_activity_logger.warning(
                f"INGREDIENT_CREATE_FAILED - User: {request.user.username} | Form errors: {form.errors}")
    else:
        form = IngredientForm()

    return render(request, 'create_ingredient.html', {'form': form})


# Custom login view with logging
def custom_login_view(request):
    """Custom login view with logging"""
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')

        from django.contrib.auth import authenticate
        user = authenticate(request, username=username, password=password)

        client_ip = get_client_ip(request)

        if user is not None:
            login(request, user)
            ActivityLogger.log_user_login(user, success=True, ip_address=client_ip)
            messages.success(request, f"Welcome back, {user.username}!")
            return redirect('dashboard')
        else:
            ActivityLogger.log_user_login(username, success=False, ip_address=client_ip)
            messages.error(request, "Invalid username or password.")

    return render(request, 'login.html')