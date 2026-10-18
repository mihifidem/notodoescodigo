from rest_framework import serializers
from .models import (
    TipoCurso, TipoRecursoTema, Curso, Tema, TemaRecurso, InscripcionCurso, Progreso,
    Quiz, Pregunta, Respuesta, ResultadoQuiz,
)


class TipoCursoSerializer(serializers.ModelSerializer):
    class Meta:
        model = TipoCurso
        fields = ['id', 'nombre', 'descripcion']


class RespuestaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Respuesta
        fields = ['id', 'texto', 'es_correcta']


class PreguntaSerializer(serializers.ModelSerializer):
    respuestas = RespuestaSerializer(many=True, read_only=True)

    class Meta:
        model = Pregunta
        fields = ['id', 'texto', 'orden', 'respuestas']


class QuizSerializer(serializers.ModelSerializer):
    preguntas = PreguntaSerializer(many=True, read_only=True)

    class Meta:
        model = Quiz
        fields = ['id', 'titulo', 'csv_filename', 'puntos_bonus', 'porcentaje_aprobacion', 'preguntas']


class TemaSerializer(serializers.ModelSerializer):
    quiz = QuizSerializer(read_only=True)
    recursos = serializers.SerializerMethodField()

    def get_recursos(self, obj):
        return TemaRecursoSerializer(obj.recursos.filter(activo=True), many=True).data

    class Meta:
        model = Tema
        fields = ['id', 'curso', 'titulo', 'contenido', 'orden', 'puntos_otorgados', 'quiz', 'recursos']


class TemaRecursoSerializer(serializers.ModelSerializer):
    tipo = serializers.CharField(source='tipo_codigo', read_only=True)
    tipo_nombre = serializers.CharField(source='tipo_nombre', read_only=True)

    class Meta:
        model = TemaRecurso
        fields = ['id', 'titulo', 'tipo', 'tipo_nombre', 'archivo', 'orden']


class CursoSerializer(serializers.ModelSerializer):
    temas = TemaSerializer(many=True, read_only=True)
    total_temas = serializers.IntegerField(read_only=True)
    tipo_curso = TipoCursoSerializer(read_only=True)

    class Meta:
        model = Curso
        fields = [
            'id', 'tipo_curso', 'nombre', 'descripcion', 'imagen',
            'fecha_inicio', 'fecha_fin', 'activo', 'destacado',
            'horas_duracion',
            'total_temas', 'temas',
        ]


class InscripcionSerializer(serializers.ModelSerializer):
    curso = CursoSerializer(read_only=True)

    class Meta:
        model = InscripcionCurso
        fields = ['id', 'curso', 'fecha_inscripcion', 'completado', 'fecha_completado']


class ProgresoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Progreso
        fields = ['id', 'tema', 'completado', 'fecha_completado']


class ResultadoQuizSerializer(serializers.ModelSerializer):
    porcentaje = serializers.IntegerField(read_only=True)

    class Meta:
        model = ResultadoQuiz
        fields = ['id', 'quiz', 'puntuacion', 'total_preguntas', 'porcentaje', 'aprobado', 'fecha']
