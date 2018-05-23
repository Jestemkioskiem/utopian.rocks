import json
import logging
import os
from beem.comment import Comment
from bson import json_util
from collections import Counter
from datetime import datetime, timedelta
from dateutil.parser import parse
from flask import Flask, jsonify, render_template
from flask_restful import Resource, Api
from pymongo import MongoClient
from statistics import mean
from webargs import fields, validate
from webargs.flaskparser import use_args, use_kwargs, parser, abort

# Score needed for a vote
MIN_SCORE = 10

# Logging
DIR_PATH = os.path.dirname(os.path.realpath(__file__))
LOGGER = logging.getLogger("utopian-io")
LOGGER.setLevel(logging.INFO)
FH = logging.FileHandler(f"{DIR_PATH}/test.log")
FH.setLevel(logging.DEBUG)
FORMATTER = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s")
FH.setFormatter(FORMATTER)
LOGGER.addHandler(FH)

# Mongo and Flask
CLIENT = MongoClient()
DB = CLIENT.utempian
app = Flask(__name__)
api = Api(app)


@app.route("/json/<json_file>")
def rewards(json_file):
    """
    Return all moderator's points for the given week.
    """
    filename = os.path.join(app.static_folder, "{}.json".format(json_file))
    try:
        with open(filename) as fp:
            data = json.load(fp)
        return jsonify(data)
    except:
        return jsonify("")


@app.route("/")
def index():
    """
    Sends all unreviewed contributions to index.html.
    """
    contributions = DB.contributions
    unreviewed = contributions.find({"status": "unreviewed"})
    return render_template("index.html", contributions=unreviewed)


def without_score(contribution):
    """
    Returns a contribution without the score.
    """
    return {x: contribution[x] for x in contribution if x != "score"}


class ContributionResource(Resource):
    """
    Endpoint for contributions in the spreadsheet.
    """
    query_parameters = {
        "category": fields.Str(),
        "status": fields.Str(),
        "author": fields.Str(),
        "moderator": fields.Str(),
        "staff_picked": fields.Bool()
    }

    @use_args(query_parameters)
    def get(self, query_parameters):
        """
        Uses the given query parameters to search for contributions in the
        database.
        """
        contributions = [json.loads(json_util.dumps(without_score(c)))
                         for c in DB.contributions.find(query_parameters)]
        return jsonify(contributions)


def string_to_date(input):
    """
    Converts a given string to a date.
    """
    if input == "today":
        return datetime.now()
    try:
        date = parse(input)
        return date
    except Exception as error:
        abort(422, errors=str(error))


def average(score):
    """
    Returns the average score of the given list of scores.
    """
    try:
        return mean(score)
    except Exception:
        return 0


def percentage(reviewed, voted):
    """
    Returns the percentage of voted contributions.
    """
    try:
        return 100.0 * voted / reviewed
    except ZeroDivisionError:
        return 100.0


def moderator_statistics(contributions):
    """
    Returns a dictionary containing statistics about all moderators.
    """
    moderators = {}
    for contribution in contributions:
        if contribution["status"] == "unreviewed":
            continue
        moderator = contribution["moderator"]

        # If contribution was submitted by banned user skip it
        if moderator == "BANNED":
            continue

        # Set default in case moderator doesn't exist
        moderators.setdefault(
            moderator, {
                "moderator": moderator,
                "category": [],
                "average_score": []
            }
        )

        # Append scores and categories
        moderators[moderator]["average_score"].append(contribution["score"])
        moderators[moderator]["category"].append(contribution["category"])

    moderator_list = []
    for moderator, value in moderators.items():
        # Set new keys and append value to list
        value["category"] = Counter(value["category"])
        value["average_score"] = average(value["average_score"])
        moderator_list.append(value)

    return {"moderators": moderator_list}


def category_statistics(contributions):
    """
    Returns a dictionary containing statistics about all categories.
    """
    categories = {}
    for contribution in contributions:
        # Don't count unreviewed contributions
        if contribution["status"] == "unreviewed":
            continue
        category = contribution["category"]

        # If contribution was task-request include it under main category
        if "task" in category:
            category = category.split("task-")[1]
            is_task = True
        else:
            is_task = False

        # Set default in case category doesn't exist
        categories.setdefault(
            category, {
                "category": category,
                "average_score": [],
                "voted": 0,
                "not_voted": 0,
                "unvoted": 0,
                "task-requests": 0,
                "moderators": [],
                "average_payout": [],
                "total_payout": 0,
            }
        )

        # Check if contribution was voted on or unvoted
        if contribution["status"] == "unvoted":
            categories[category]["unvoted"] += 1
            categories[category]["not_voted"] += 1
        elif contribution["voted_on"]:
            categories[category]["voted"] += 1
        else:
            categories[category]["not_voted"] += 1

        # Check if contribution was a task request
        if is_task:
            categories[category]["task-requests"] += 1

        # Add moderator, score and total payout in SBD
        categories[category]["moderators"].append(contribution["moderator"])
        categories[category]["average_score"].append(contribution["score"])
        categories[category]["total_payout"] += contribution["total_payout"]

    category_list = []
    for category, value in categories.items():
        # Set new keys and append value to list
        value["reviewed"] = value["voted"] + value["not_voted"]
        value["average_score"] = average(value["average_score"])
        value["moderators"] = Counter(value["moderators"])
        value["average_payout"] = value["total_payout"] / value["reviewed"]
        value["pct_voted"] = percentage(value["reviewed"], value["voted"])
        category_list.append(value)

    return {"categories": category_list}


def project_statistics(contributions):
    """
    Returns a dictionary containing statistics about all projects.
    """
    projects = {}
    for contribution in contributions:
        # Don't count unreviewed contributions
        if contribution["status"] == "unreviewed":
            continue
        project = contribution["repository"]

        # Set default in case category doesn't exist
        projects.setdefault(
            project, {
                "project": project,
                "average_score": [],
                "voted": 0,
                "not_voted": 0,
                "unvoted": 0,
                "task-requests": 0,
                "moderators": [],
                "average_payout": [],
                "total_payout": 0
            }
        )

        # Check if contribution was voted on or unvoted
        if contribution["status"] == "unvoted":
            projects[project]["unvoted"] += 1
            projects[project]["not_voted"] += 1
        elif contribution["voted_on"]:
            projects[project]["voted"] += 1
        else:
            projects[project]["not_voted"] += 1

        # If contribution was a task request count this
        if "task" in contribution["category"]:
            projects[project]["task-requests"] += 1

        # Add moderator and score
        projects[project]["moderators"].append(contribution["moderator"])
        projects[project]["average_score"].append(contribution["score"])
        projects[project]["total_payout"] += contribution["total_payout"]

    project_list = []
    for project, value in projects.items():
        # Set new keys and append value to list
        value["reviewed"] = value["voted"] + value["not_voted"]
        value["average_score"] = average(value["average_score"])
        value["average_payout"] = value["total_payout"] / value["reviewed"]
        value["moderators"] = Counter(value["moderators"])
        value["pct_voted"] = percentage(value["reviewed"], value["voted"])
        project_list.append(value)

    return {"projects": project_list}


def staff_pick_statistics(contributions):
    """
    Returns a list of contributions that were staff picked.
    """
    staff_picks = []
    for contribution in contributions:
        # If contribution wasn't staff picked skip it
        if not contribution["staff_picked"]:
            continue

        staff_picks.append(contribution)

    return {"staff_picks": staff_picks}


def task_request_statistics(contributions):
    """
    Returns a list of task requests.
    """
    task_requests = []
    for contribution in contributions:
        # If contribution wasn't staff picked skip it
        if "task" in contribution["category"]:
            task_requests.append(contribution)

    return {"task_requests": task_requests}


class WeeklyResource(Resource):
    """
    Endpoint for weekly contribution data (requested).
    """
    def get(self, date):
        LOGGER.info(f"Retrieving for {date}")
        try:
            # Get date for retrieving posts
            date = string_to_date(date)
            week_ago = date - timedelta(days=7)

            # Retrieve contributions made in week before the given date
            contributions = DB.contributions
            pipeline = [{"$match": {"review_date": {"$gt": week_ago}}}]
            contributions = [json.loads(json_util.dumps(c))
                             for c in contributions.aggregate(pipeline)]

            moderators = moderator_statistics(contributions)
            categories = category_statistics(contributions)
            projects = project_statistics(contributions)
            staff_picks = staff_pick_statistics(contributions)
            task_requests = task_request_statistics(contributions)

            return jsonify(
                [moderators, categories, projects, staff_picks, task_requests])
        except Exception as error:
            LOGGER.error(error)


api.add_resource(WeeklyResource, "/api/statistics/<string:date>")
api.add_resource(ContributionResource, "/api/posts")


def staff_pick_section(staff_picks):
    section = ""
    for staff_pick in staff_picks["staff_picks"]:
        url = staff_pick["url"]
        post = Comment(url)
        title = post.json()["title"]
        author = staff_pick['author']
        category = staff_pick['category']
        section += (
            f"[{title}]({url}) by {author} [{category}]\n"
            "[Image (contributor profile image / image from the post)]\n\n"
            "[Paragraph: Background info on project etc.]\n\n"
            "[Paragraph: CM review, including etc.]\n\n"
            f"Total payout: {staff_pick['total_payout']}\n"
            f"Number of votes: {staff_pick['total_votes']}\n\n"
        )

    print(section)
    return section


def post_statistics_section():
    pass


@app.route("/weekly")
def weekly():
    """
    Returns weekly statistics in a format that can be posted on Steemit.
    """
    week_ago = datetime.now() - timedelta(days=7)
    contributions = DB.contributions
    pipeline = [{"$match": {"review_date": {"$gt": week_ago}}}]
    contributions = [json.loads(json_util.dumps(c))
                     for c in contributions.aggregate(pipeline)]

    # categories = category_statistics(contributions)
    staff_picks = staff_pick_statistics(contributions)
    return staff_pick_section(staff_picks)


def main():
    app.run(host="0.0.0.0")


if __name__ == '__main__':
    print(DIR_PATH)
    main()
