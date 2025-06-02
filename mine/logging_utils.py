import logging
from django.contrib.auth.models import User
from django.utils import timezone
from functools import wraps
import json

# Get loggers
meal_serve_logger = logging.getLogger('meal_serve')
delivery_logger = logging.getLogger('delivery')
ingredient_logger = logging.getLogger('ingredient')
user_activity_logger = logging.getLogger('user_activity')
security_logger = logging.getLogger('security')


class ActivityLogger:
    """Centralized logging utility for the meal and inventory system"""

    @staticmethod
    def log_meal_serve(user, meal, quantity, success=True, error_msg=None):
        """Log meal serving activities"""
        timestamp = timezone.now().strftime('%Y-%m-%d %H:%M:%S')

        if success:
            meal_serve_logger.info(
                f"MEAL_SERVED - User: {user.username} | Meal: {meal.name} | "
                f"Quantity: {quantity} portions | Time: {timestamp}"
            )
            user_activity_logger.info(
                f"USER_ACTION - {user.username} served {quantity} portions of {meal.name}"
            )
        else:
            meal_serve_logger.error(
                f"MEAL_SERVE_FAILED - User: {user.username} | Meal: {meal.name} | "
                f"Attempted Quantity: {quantity} | Error: {error_msg} | Time: {timestamp}"
            )

    @staticmethod
    def log_delivery(user, ingredient, quantity, success=True, error_msg=None):
        """Log delivery activities"""
        timestamp = timezone.now().strftime('%Y-%m-%d %H:%M:%S')

        if success:
            delivery_logger.info(
                f"DELIVERY_RECORDED - User: {user.username} | Ingredient: {ingredient.name} | "
                f"Quantity: {quantity}g | New Stock: {ingredient.stock_quantity}g | Time: {timestamp}"
            )
            user_activity_logger.info(
                f"USER_ACTION - {user.username} recorded delivery of {quantity}g {ingredient.name}"
            )
        else:
            delivery_logger.error(
                f"DELIVERY_FAILED - User: {user.username} | Ingredient: {ingredient.name} | "
                f"Attempted Quantity: {quantity}g | Error: {error_msg} | Time: {timestamp}"
            )

    @staticmethod
    def log_ingredient_action(user, action, ingredient, old_data=None, new_data=None):
        """Log ingredient CRUD operations"""
        timestamp = timezone.now().strftime('%Y-%m-%d %H:%M:%S')

        if action == 'CREATE':
            ingredient_logger.info(
                f"INGREDIENT_CREATED - User: {user.username} | Ingredient: {ingredient.name} | "
                f"Initial Stock: {ingredient.stock_quantity}g | Time: {timestamp}"
            )
        elif action == 'UPDATE':
            ingredient_logger.info(
                f"INGREDIENT_UPDATED - User: {user.username} | Ingredient: {ingredient.name} | "
                f"Old Stock: {old_data.get('stock_quantity', 'N/A')}g | "
                f"New Stock: {ingredient.stock_quantity}g | Time: {timestamp}"
            )
        elif action == 'DELETE':
            ingredient_logger.info(
                f"INGREDIENT_DELETED - User: {user.username} | Ingredient: {ingredient.name} | "
                f"Final Stock: {ingredient.stock_quantity}g | Time: {timestamp}"
            )

        user_activity_logger.info(
            f"USER_ACTION - {user.username} {action.lower()}d ingredient {ingredient.name}"
        )

    @staticmethod
    def log_meal_action(user, action, meal, ingredients_count=0):
        """Log meal CRUD operations"""
        timestamp = timezone.now().strftime('%Y-%m-%d %H:%M:%S')

        if action == 'CREATE':
            ingredient_logger.info(
                f"MEAL_CREATED - User: {user.username} | Meal: {meal.name} | "
                f"Ingredients: {ingredients_count} | Time: {timestamp}"
            )
        elif action == 'UPDATE':
            ingredient_logger.info(
                f"MEAL_UPDATED - User: {user.username} | Meal: {meal.name} | "
                f"Ingredients: {ingredients_count} | Time: {timestamp}"
            )
        elif action == 'DELETE':
            ingredient_logger.info(
                f"MEAL_DELETED - User: {user.username} | Meal: {meal.name} | Time: {timestamp}"
            )

        user_activity_logger.info(
            f"USER_ACTION - {user.username} {action.lower()}d meal {meal.name}"
        )

    @staticmethod
    def log_user_login(user, success=True, ip_address=None):
        """Log user authentication"""
        timestamp = timezone.now().strftime('%Y-%m-%d %H:%M:%S')

        if success:
            user_activity_logger.info(
                f"USER_LOGIN - User: {user.username} | IP: {ip_address} | Time: {timestamp}"
            )
        else:
            security_logger.warning(
                f"LOGIN_FAILED - Username: {user} | IP: {ip_address} | Time: {timestamp}"
            )

    @staticmethod
    def log_permission_denied(user, action, resource):
        """Log permission denied attempts"""
        timestamp = timezone.now().strftime('%Y-%m-%d %H:%M:%S')
        security_logger.warning(
            f"PERMISSION_DENIED - User: {user.username} | Action: {action} | "
            f"Resource: {resource} | Time: {timestamp}"
        )

    @staticmethod
    def log_stock_alert(ingredient, threshold_type="LOW"):
        """Log stock alerts"""
        timestamp = timezone.now().strftime('%Y-%m-%d %H:%M:%S')
        ingredient_logger.warning(
            f"STOCK_ALERT - {threshold_type}_STOCK | Ingredient: {ingredient.name} | "
            f"Current Stock: {ingredient.stock_quantity}g | Time: {timestamp}"
        )


def log_user_activity(action_description):
    """Decorator to automatically log user activities"""

    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            user = getattr(request, 'user', None)
            if user and user.is_authenticated:
                user_activity_logger.info(
                    f"USER_ACCESS - {user.username} accessed {action_description}"
                )
            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator


def get_client_ip(request):
    """Get client IP address from request"""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip