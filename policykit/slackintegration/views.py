from django.shortcuts import render
from django.http import HttpResponse
from urllib import parse
import urllib.request
from policykit.settings import CLIENT_SECRET
from django.contrib.auth import login, authenticate
import logging
from django.shortcuts import redirect
import json
from slackintegration.models import *
from policyengine.models import CommunityAction, UserVote, CommunityAPI, CommunityPolicy, Proposal
from policyengine.views import check_filter_code, check_policy_code
from django.contrib.auth.models import User, Group
from django.views.decorators.csrf import csrf_exempt

logger = logging.getLogger(__name__)

# Create your views here.

def oauth(request):
    code = request.GET.get('code')
    state = request.GET.get('state')
    
    data = parse.urlencode({
        'client_id': '455205644210.932801604965',
        'client_secret': CLIENT_SECRET,
        'code': code,
        }).encode()
        
    req = urllib.request.Request('https://slack.com/api/oauth.v2.access', data=data)
    resp = urllib.request.urlopen(req)
    res = json.loads(resp.read().decode('utf-8'))
    
    logger.info(res)
    
    if res['ok']:
        if state =="user": 
            user = authenticate(request, oauth=res)
            if user:
                login(request, user)
                
        elif state == "app":
            s = SlackIntegration.objects.filter(team_id=res['team']['id'])
            user_group,_ = Group.objects.get_or_create(name="Slack")
            if not s.exists():
                _ = SlackIntegration.objects.create(
                    community_name=res['team']['name'],
                    team_id=res['team']['id'],
                    access_token=res['access_token'],
                    user_group=user_group
                    )
            else:
                s[0].community_name = res['team']['name']
                s[0].team_id = res['team']['id']
                s[0].access_token = res['access_token']
                s[0].save()
    else:
        # error message stating that the sign-in/add-to-slack didn't work
        response = redirect('/login?error=cancel')
        return response
        
    response = redirect('/login?success=true')
    return response


@csrf_exempt
def action(request):
    json_data = json.loads(request.body)

    logger.info("SLACK EVENT")
    logger.info(json_data)
    action_type = json_data.get('type')
    
    if action_type == "url_verification":
        challenge = json_data.get('challenge')
        return HttpResponse(challenge)
    
    elif action_type == "event_callback":
        event = json_data.get('event')
        team_id = json_data.get('team_id')
        integration = SlackIntegration.objects.get(team_id=team_id)
        author = SlackUser.objects.all()[0] # TODO Change this to admin user? Bot user?
#         author_id = json_data.get('authed_users')[0]

        new_action = None
        
        if event.get('type') == "channel_rename":
            new_action = SlackRenameConversation()
            new_action.community_integration = integration
            new_action.initiator = author
            new_action.name = event['channel']['name']
            new_action.channel = event['channel']['id']
        elif event.get('type') == "member_joined_channel":
            new_action = SlackJoinConversation()
            new_action.community_integration = integration
            new_action.inviter_user = event.get('inviter')
            new_action.initiator = author
            new_action.users = event.get('user')
            new_action.channel = event['channel']
        elif event.get('type') == 'message' and event.get('subtype') == None:
            new_action = SlackPostMessage()
            new_action.community_integration = integration
            new_action.initiator = author
            new_action.text = event['text']
            new_action.channel = event['channel']
            new_action.time_stamp = event['ts']
            new_action.poster = event['user']
        elif event.get('type') == 'pin_added':
            new_action = SlackPinMessage()
            new_action.community_integration = integration
            new_action.initiator = author
            new_action.channel = event['channel_id']
            new_action.timestamp = event['item']['message']['ts']
            user = event['user']
            new_action.save(user=user)

        elif event.get('type') == 'channel_archive':
            new_action = SlackArchiveChannel()
            new_action.community_integration = integration
            new_action.author = author
            new_action.channel = event['channel']
            user = event['user']
            new_action.save(user=user)

        elif event.get('type') == 'channel_created':
            new_action = SlackCreateChannel()
            new_action.community_integration = integration
            new_action.author = author
            new_action.channel = event['channel']['id']
            creator = event['channel']['creator']
            new_action.save(creator=creator)
        
        if new_action:
            for policy in CommunityPolicy.objects.filter(proposal__status=Proposal.PASSED, community_integration=new_action.community_integration):
                if check_filter_code(policy, new_action):
                    new_action.community_origin = True
                    new_action.save()
                    cond_result = check_policy_code(policy, new_action.communityaction)
                    if cond_result == Proposal.PROPOSED or cond_result == Proposal.FAILED:
                        new_action.revert()


        if event.get('type') == 'reaction_added':
            ts = event['item']['ts']
            api_action = CommunityAPI.objects.filter(community_post=ts)
            if api_action:
                api_action = api_action[0]
                action = CommunityAction.objects.filter(api_action=api_action.id)
                if action:
                    action = action[0]
                    if event['reaction'] == '+1' or event['reaction'] == '-1':
                        if event['reaction'] == '+1':
                            value = True
                        elif event['reaction'] == '-1':
                            value = False
                        
                        user = SlackUser.objects.get(user_id=event['user'])
                        uv, created = UserVote.objects.get_or_create(proposal=action.proposal,
                                                                     user=user)
                        uv.boolean_value = value
                        uv.save()
    
    return HttpResponse("")
    
