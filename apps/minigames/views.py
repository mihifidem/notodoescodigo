from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView


class MinigamesHubView(LoginRequiredMixin, TemplateView):
    template_name = 'minigames/hub.html'


class NexusMindView(LoginRequiredMixin, TemplateView):
    template_name = 'minigames/memory.html'


class CodeFlowView(LoginRequiredMixin, TemplateView):
    template_name = 'minigames/logic.html'


class SynapseShotView(LoginRequiredMixin, TemplateView):
    template_name = 'minigames/skill.html'


class GridMindView(LoginRequiredMixin, TemplateView):
    template_name = 'minigames/gridmind.html'


class StroopXView(LoginRequiredMixin, TemplateView):
    template_name = 'minigames/stroop.html'


class MathBlitzView(LoginRequiredMixin, TemplateView):
    template_name = 'minigames/mathblitz.html'


class WordSprintView(LoginRequiredMixin, TemplateView):
    template_name = 'minigames/wordsprint.html'


class ReactionPulseView(LoginRequiredMixin, TemplateView):
    template_name = 'minigames/reactionpulse.html'


class EchoGridView(LoginRequiredMixin, TemplateView):
    template_name = 'minigames/echogrid.html'


class NBackFlowView(LoginRequiredMixin, TemplateView):
    template_name = 'minigames/nback.html'


class ShadowMatchView(LoginRequiredMixin, TemplateView):
    template_name = 'minigames/shadowmatch.html'


class EstimaRapidView(LoginRequiredMixin, TemplateView):
    template_name = 'minigames/estimarapid.html'
